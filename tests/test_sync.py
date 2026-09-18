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
    return f'<html><div class="list_right"><ul>{rows}</ul></div>{next_link}</html>'


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


def test_default_sync_fetches_requested_jxxx_list_url(tmp_path) -> None:
    requested_url = "https://jwc.xjtu.edu.cn/jxxx/jxtz2.htm"
    session = FakeSession({requested_url: Response(200, list_page([]))})

    NoticeSyncService(session, make_store(tmp_path)).sync()

    assert session.calls == [requested_url]


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

    result = NoticeSyncService(
        session, make_store(tmp_path), list_url=start, cutoff="2026-01-01"
    ).sync()

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
    NoticeSyncService(first_session, store, list_url=start).sync()
    second_session = FakeSession(pages)
    result = NoticeSyncService(second_session, store, list_url=start).sync()
    assert url("1") not in second_session.calls
    assert second_session.calls.count(url("2")) == 1
    assert result["empty"] == 1

    changed = dict(pages)
    changed[start] = Response(200, list_page([("1", "通知（更正）", "教学运行", "2026-09-16")]))
    changed[url("1")] = Response(
        200, detail("1", "通知（更正）", "教学运行", "2026-09-16", "更新正文")
    )
    metadata_session = FakeSession(changed)
    NoticeSyncService(metadata_session, store, list_url=start).sync()
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
    NoticeSyncService(FakeSession(initial), store, list_url=start).sync()
    failing = {
        start: Response(200, list_page([("1", "通知更正", "教学运行", "2026-09-16")])),
        url("1"): Response(500, "server error"),
    }
    result = NoticeSyncService(FakeSession(failing), store, list_url=start).sync()
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
        NoticeSyncService(FakeSession(interrupted), interrupted_store, list_url=start).sync()
    assert interrupted_store.get_notice("1")["body_text"] == "一正文"
    resumed = dict(interrupted)
    resumed[url("2")] = Response(200, detail("2", "二", "教学运行", "2026-09-15", "二正文"))
    NoticeSyncService(FakeSession(resumed), interrupted_store, list_url=start).sync()
    assert interrupted_store.get_notice("2")["body_text"] == "二正文"


def test_interrupted_initial_backfill_continues_past_known_pages_on_resume(tmp_path) -> None:
    start = "https://jwc.xjtu.edu.cn/jxtz2.htm"
    page_two = "https://jwc.xjtu.edu.cn/jxtz2.htm?PAGENUM=2"
    page_three = "https://jwc.xjtu.edu.cn/jxtz2.htm?PAGENUM=3"
    page_four = "https://jwc.xjtu.edu.cn/jxtz2.htm?PAGENUM=4"
    store = make_store(tmp_path)
    first = {
        start: Response(
            200, list_page([("1", "第一页", "教学运行", "2026-09-15")], "jxtz2.htm?PAGENUM=2")
        ),
        page_two: Response(
            200, list_page([("2", "第二页", "教学运行", "2026-09-14")], "jxtz2.htm?PAGENUM=3")
        ),
        page_three: KeyboardInterrupt(),
        url("1"): Response(200, detail("1", "第一页", "教学运行", "2026-09-15", "第一页正文")),
        url("2"): Response(200, detail("2", "第二页", "教学运行", "2026-09-14", "第二页正文")),
    }
    with pytest.raises(KeyboardInterrupt):
        NoticeSyncService(FakeSession(first), store, list_url=start).sync()

    resumed = dict(first)
    resumed[page_three] = Response(
        200, list_page([("3", "第三页", "教学运行", "2026-09-13")], "jxtz2.htm?PAGENUM=4")
    )
    resumed[page_four] = Response(200, list_page([]))
    resumed[url("3")] = Response(
        200, detail("3", "第三页", "教学运行", "2026-09-13", "第三页正文")
    )
    session = FakeSession(resumed)
    NoticeSyncService(session, store, list_url=start).sync()

    assert page_three in session.calls
    assert store.get_notice("3")["body_text"] == "第三页正文"


def test_interrupted_changed_notice_is_marked_for_retry_before_detail_fetch(tmp_path) -> None:
    start = "https://jwc.xjtu.edu.cn/jxtz2.htm"
    store = make_store(tmp_path)
    initial = {
        start: Response(200, list_page([("1", "原通知", "教学运行", "2026-09-15")])),
        url("1"): Response(200, detail("1", "原通知", "教学运行", "2026-09-15", "旧正文")),
    }
    NoticeSyncService(
        FakeSession(initial), store, list_url=start, refresh_after_days=None
    ).sync()

    changed = {
        start: Response(200, list_page([("1", "更正通知", "教学运行", "2026-09-16")])),
        url("1"): KeyboardInterrupt(),
    }
    with pytest.raises(KeyboardInterrupt):
        NoticeSyncService(
            FakeSession(changed), store, list_url=start, refresh_after_days=None
        ).sync()
    assert store.get_notice("1")["body_text"] == "旧正文"
    assert store.get_notice("1")["fetch_status"] == "failed"

    resumed = dict(changed)
    resumed[url("1")] = Response(
        200, detail("1", "更正通知", "教学运行", "2026-09-16", "新正文")
    )
    NoticeSyncService(
        FakeSession(resumed), store, list_url=start, refresh_after_days=None
    ).sync()
    assert store.get_notice("1")["body_text"] == "新正文"


def test_failed_deep_notice_is_retried_before_quiet_page_stopping(tmp_path) -> None:
    start = "https://jwc.xjtu.edu.cn/jxtz2.htm"
    store = make_store(tmp_path)
    store.upsert_detail(
        notice_id="999",
        title="深页通知",
        category="教学运行",
        published_date="2026-02-01",
        source_url=url("999"),
        body_text="旧正文",
        body_html="<p>旧正文</p>",
        fetched_at="2026-09-01T00:00:00Z",
    )
    run_id = store.begin_sync_run(started_at="2026-09-01T00:00:00Z")
    store.finish_sync_run(
        run_id,
        status="completed",
        stats={},
        finished_at="2026-09-01T00:01:00Z",
        backfill_completed=True,
    )
    store.record_detail_failure(
        "999", "temporary failure", run_id=None, occurred_at="2026-09-02T00:00:00Z"
    )
    session = FakeSession(
        {
            start: Response(200, list_page([])),
            url("999"): Response(
                200, detail("999", "深页通知", "教学运行", "2026-02-01", "恢复正文")
            ),
        }
    )

    result = NoticeSyncService(
        session, store, list_url=start, refresh_after_days=None
    ).sync()

    assert session.calls[0] == url("999")
    assert store.get_notice("999")["body_text"] == "恢复正文"
    assert result["ok"] == 1


def test_stale_deep_notice_is_refreshed_before_quiet_page_stopping(tmp_path) -> None:
    start = "https://jwc.xjtu.edu.cn/jxtz2.htm"
    store = make_store(tmp_path)
    store.upsert_detail(
        notice_id="998",
        title="深页旧通知",
        category="教学运行",
        published_date="2026-02-01",
        source_url=url("998"),
        body_text="旧正文",
        body_html="<p>旧正文</p>",
        fetched_at="2026-09-01T00:00:00Z",
    )
    run_id = store.begin_sync_run(started_at="2026-09-01T00:00:00Z")
    store.finish_sync_run(
        run_id,
        status="completed",
        stats={},
        finished_at="2026-09-01T00:01:00Z",
        backfill_completed=True,
    )
    session = FakeSession(
        {
            start: Response(200, list_page([])),
            url("998"): Response(
                200, detail("998", "深页旧通知", "教学运行", "2026-02-01", "刷新正文")
            ),
        }
    )

    result = NoticeSyncService(
        session,
        store,
        list_url=start,
        refresh_after_days=7,
        now=lambda: "2026-09-18T00:00:00Z",
    ).sync()

    assert session.calls[0] == url("998")
    assert store.get_notice("998")["body_text"] == "刷新正文"
    assert result["ok"] == 1


def test_empty_refresh_of_existing_body_counts_as_failure(tmp_path) -> None:
    start = "https://jwc.xjtu.edu.cn/jxtz2.htm"
    store = make_store(tmp_path)
    initial = {
        start: Response(200, list_page([("1", "通知", "教学运行", "2026-09-15")])),
        url("1"): Response(200, detail("1", "通知", "教学运行", "2026-09-15", "旧正文")),
    }
    NoticeSyncService(FakeSession(initial), store, list_url=start).sync()
    changed = {
        start: Response(200, list_page([("1", "通知更正", "教学运行", "2026-09-16")])),
        url("1"): Response(200, detail("1", "通知更正", "教学运行", "2026-09-16", "")),
    }

    result = NoticeSyncService(
        FakeSession(changed), store, list_url=start, refresh_after_days=None
    ).sync()

    assert result["failed"] == 1
    assert result["empty"] == 0
    assert store.get_notice("1")["body_text"] == "旧正文"
    assert store.sync_status()["last_run"]["status"] == "failed"


def test_detail_failure_marks_run_failed_and_keeps_backfill_pending(tmp_path) -> None:
    start = "https://jwc.xjtu.edu.cn/jxtz2.htm"
    store = make_store(tmp_path)
    session = FakeSession(
        {
            start: Response(200, list_page([("1", "通知", "教学运行", "2026-09-15")])),
            url("1"): Response(503, "unavailable"),
        }
    )

    result = NoticeSyncService(session, store, list_url=start).sync()

    assert result["failed"] == 1
    assert store.sync_status()["last_run"]["status"] == "failed"
    assert store.initial_backfill_complete() is False
