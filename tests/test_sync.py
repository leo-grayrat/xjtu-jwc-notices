from dataclasses import dataclass

import pytest

from xjtu_jwc_notices.storage import NoticeStore
from xjtu_jwc_notices.sync import NoticeSyncService


@dataclass
class Response:
    status_code: int
    text: str


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, BaseException):
            raise response
        return response


def list_page(entries, next_url=None):
    rows = "".join(
        f'<li><span class="category">{category}</span><a href="/info/1033/{notice_id}.htm">'
        f"{title}</a><span>{published}</span></li>"
        for notice_id, title, category, published in entries
    )
    next_link = f'<a href="{next_url}">下一页</a>' if next_url else ""
    return f"<html><ul>{rows}</ul>{next_link}</html>"


def detail(notice_id, title, category, published, body):
    return (
        f'<html><h1>{title}</h1><div class="meta">发布时间：{published} 分类：{category}</div>'
        f'<div id="vsb_content"><p>{body}</p></div></html>'
    )


def url(notice_id):
    return f"https://jwc.xjtu.edu.cn/info/1033/{notice_id}.htm"


def make_store(tmp_path):
    store = NoticeStore(tmp_path / "notices.sqlite3")
    store.initialize()
    return store


def test_initial_sync_honors_cutoff_and_only_follows_discovered_pages(tmp_path) -> None:
    start = "https://jwc.xjtu.edu.cn/jxtz2.htm"
    page_two = "https://jwc.xjtu.edu.cn/jxtz2.htm?PAGENUM=2"
    page_three = "https://jwc.xjtu.edu.cn/jxtz2.htm?PAGENUM=3"
    page_four = "https://jwc.xjtu.edu.cn/jxtz2.htm?PAGENUM=4"
    session = FakeSession(
        {
            start: Response(
                200, list_page([("1", "新通知", "教学运行", "2026-01-02")], "jxtz2.htm?PAGENUM=2")
            ),
            page_two: Response(
                200, list_page([("9", "旧通知", "教学运行", "2025-12-31")], "jxtz2.htm?PAGENUM=3")
            ),
            page_three: Response(
                200, list_page([("8", "更旧通知", "教学运行", "2025-12-30")], "jxtz2.htm?PAGENUM=4")
            ),
            page_four: Response(200, list_page([])),
            url("1"): Response(200, detail("1", "新通知", "教学运行", "2026-01-02", "正文")),
        }
    )

    result = NoticeSyncService(session, make_store(tmp_path), cutoff="2026-01-01").sync()

    assert result["processed"] == 1
    assert url("1") in session.calls
    assert url("9") not in session.calls
    assert [call for call in session.calls if "jxtz2.htm" in call] == [start, page_two, page_three]
    assert page_four not in session.calls


def test_second_sync_is_idempotent_then_refetches_metadata_changes_and_empty_bodies(
    tmp_path,
) -> None:
    start = "https://jwc.xjtu.edu.cn/jxtz2.htm"
    page_two = "https://jwc.xjtu.edu.cn/jxtz2.htm?PAGENUM=2"
    pages = {
        start: Response(
            200,
            list_page(
                [
                    ("1", "通知", "教学运行", "2026-09-15"),
                    ("2", "空通知", "教学运行", "2026-09-14"),
                ],
                "jxtz2.htm?PAGENUM=2",
            ),
        ),
        page_two: Response(200, list_page([])),
        url("1"): Response(200, detail("1", "通知", "教学运行", "2026-09-15", "正文")),
        url("2"): Response(200, detail("2", "空通知", "教学运行", "2026-09-14", "")),
    }
    store = make_store(tmp_path)
    first_session = FakeSession(pages)
    NoticeSyncService(first_session, store).sync()
    second_session = FakeSession(pages)
    result = NoticeSyncService(second_session, store).sync()
    assert url("1") not in second_session.calls
    assert second_session.calls.count(url("2")) == 1
    assert result["empty"] == 1

    changed = dict(pages)
    changed[start] = Response(200, list_page([("1", "通知（更正）", "教学运行", "2026-09-16")]))
    changed[url("1")] = Response(
        200, detail("1", "通知（更正）", "教学运行", "2026-09-16", "更新正文")
    )
    metadata_session = FakeSession(changed)
    NoticeSyncService(metadata_session, store).sync()
    assert url("1") in metadata_session.calls
    assert store.get_notice("1")["body_text"] == "更新正文"


def test_detail_failure_preserves_old_body_and_interrupted_run_resumes_completed_items(
    tmp_path,
) -> None:
    start = "https://jwc.xjtu.edu.cn/jxtz2.htm"
    store = make_store(tmp_path)
    initial = {
        start: Response(200, list_page([("1", "通知", "教学运行", "2026-09-15")])),
        url("1"): Response(200, detail("1", "通知", "教学运行", "2026-09-15", "旧正文")),
    }
    NoticeSyncService(FakeSession(initial), store).sync()
    failing = {
        start: Response(200, list_page([("1", "通知更正", "教学运行", "2026-09-16")])),
        url("1"): Response(500, "server error"),
    }
    result = NoticeSyncService(FakeSession(failing), store).sync()
    assert result["failed"] == 1
    assert store.get_notice("1")["body_text"] == "旧正文"
    assert store.get_notice("1")["fetch_status"] == "failed"

    interrupted = {
        start: Response(
            200,
            list_page(
                [("1", "一", "教学运行", "2026-09-15"), ("2", "二", "教学运行", "2026-09-15")]
            ),
        ),
        url("1"): Response(200, detail("1", "一", "教学运行", "2026-09-15", "一正文")),
        url("2"): KeyboardInterrupt(),
    }
    interrupted_store = make_store(tmp_path / "interrupted")
    with pytest.raises(KeyboardInterrupt):
        NoticeSyncService(FakeSession(interrupted), interrupted_store).sync()
    assert interrupted_store.get_notice("1")["body_text"] == "一正文"
    resumed = dict(interrupted)
    resumed[url("2")] = Response(200, detail("2", "二", "教学运行", "2026-09-15", "二正文"))
    NoticeSyncService(FakeSession(resumed), interrupted_store).sync()
    assert interrupted_store.get_notice("2")["body_text"] == "二正文"
