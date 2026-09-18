from datetime import date

from xjtu_jwc_notices.storage import NoticeStore


def test_initialize_upserts_detail_and_preserves_stable_notice_metadata(tmp_path) -> None:
    store = NoticeStore(tmp_path / "notices.sqlite3")
    store.initialize()

    store.upsert_detail(
        notice_id="100",
        title="选课通知",
        category="教学运行",
        published_date=date(2026, 9, 15),
        source_url="HTTPS://JWC.XJTU.EDU.CN/info/1033/100.htm#top",
        body_text="请按时选课。",
        body_html="<p>请按时选课。</p>",
        fetched_at="2026-09-17T00:00:00Z",
    )
    store.upsert_detail(
        notice_id="100",
        title="选课通知（更新）",
        category="教学运行",
        published_date=date(2026, 9, 16),
        source_url="https://jwc.xjtu.edu.cn/info/1033/100.htm",
        body_text="请按时选课。",
        body_html="<p>请按时选课。</p>",
        fetched_at="2026-09-17T01:00:00Z",
    )

    notice = store.get_notice("100")
    assert notice["notice_id"] == "100"
    assert notice["title"] == "选课通知（更新）"
    assert notice["published_date"] == "2026-09-16"
    assert notice["source_url"] == "https://jwc.xjtu.edu.cn/info/1033/100.htm"
    assert notice["fetch_status"] == "ok"
    assert notice["content_hash"]
    assert notice["first_seen_at"] == "2026-09-17T00:00:00Z"
    assert notice["last_seen_at"] == "2026-09-17T01:00:00Z"
    assert notice["fetched_at"] == "2026-09-17T01:00:00Z"


def test_empty_detail_failure_and_integrity_do_not_destroy_existing_body(tmp_path) -> None:
    store = NoticeStore(tmp_path / "notices.sqlite3")
    store.initialize()
    store.upsert_detail(
        notice_id="100",
        title="通知",
        category="教学运行",
        published_date="2026-09-15",
        source_url="https://jwc.xjtu.edu.cn/info/1033/100.htm",
        body_text="旧正文",
        body_html="<p>旧正文</p>",
        fetched_at="2026-09-17T00:00:00Z",
    )
    store.record_detail_failure("100", "timeout", run_id=None, occurred_at="2026-09-17T01:00:00Z")
    assert store.get_notice("100")["body_text"] == "旧正文"
    assert store.get_notice("100")["fetch_status"] == "failed"

    store.upsert_detail(
        notice_id="101",
        title="空正文",
        category="竞赛安排",
        published_date="2026-09-14",
        source_url="https://jwc.xjtu.edu.cn/info/1033/101.htm",
        body_text="",
        body_html="",
        fetched_at="2026-09-17T01:00:00Z",
    )
    assert store.get_notice("101")["fetch_status"] == "empty_body"
    assert store.integrity_check()["ok"] is True


def test_search_categories_status_and_opaque_pagination(tmp_path) -> None:
    store = NoticeStore(tmp_path / "notices.sqlite3")
    store.initialize()
    for notice_id, title, category, published, body in [
        ("1", "选课通知", "教学运行", "2026-09-15", "秋季选课安排"),
        ("2", "竞赛通知", "竞赛安排", "2026-09-14", "数学竞赛报名"),
        ("3", "补退选通知", "教学运行", "2026-09-13", "选课补退选"),
    ]:
        store.upsert_detail(
            notice_id=notice_id,
            title=title,
            category=category,
            published_date=published,
            source_url=f"https://jwc.xjtu.edu.cn/info/1033/{notice_id}.htm",
            body_text=body,
            body_html=f"<p>{body}</p>",
            fetched_at="2026-09-17T00:00:00Z",
        )

    first = store.search(query="选课", date_from="2026-09-13", categories=["教学运行"], limit=1)
    assert [row["notice_id"] for row in first["items"]] == ["1"]
    assert first["items"][0]["snippet"]
    assert first["next_cursor"]
    second = store.search(query="选课", categories=["教学运行"], cursor=first["next_cursor"])
    assert [row["notice_id"] for row in second["items"]] == ["3"]
    assert second["data_as_of"] == "2026-09-17T00:00:00Z"

    categories = {row["category"]: row for row in store.list_categories()}
    assert categories["教学运行"]["count"] == 2
    assert categories["教学运行"]["newest_date"] == "2026-09-15"
    assert store.sync_status()["notice_count"] == 3
    assert store.search(limit=51)["items"]  # limit is capped rather than unbounded


def test_search_escapes_fts_syntax_but_keeps_literal_query_matching(tmp_path) -> None:
    store = NoticeStore(tmp_path / "notices.sqlite3")
    store.initialize()
    for notice_id, title, body in [
        ("cpp", "C++ 课程", "C++ 课程说明"),
        ("quote", '带引号的通知', '请阅读 "重要" 说明'),
    ]:
        store.upsert_detail(
            notice_id=notice_id,
            title=title,
            category="教学运行",
            published_date="2026-09-15",
            source_url=f"https://jwc.xjtu.edu.cn/info/1033/{notice_id}.htm",
            body_text=body,
            body_html=f"<p>{body}</p>",
            fetched_at="2026-09-17T00:00:00Z",
        )

    assert [row["notice_id"] for row in store.search(query="C++")["items"]] == ["cpp"]
    assert [row["notice_id"] for row in store.search(query='"重要"')["items"]] == ["quote"]


def test_empty_refresh_preserves_existing_archive_and_records_retryable_failure(tmp_path) -> None:
    store = NoticeStore(tmp_path / "notices.sqlite3")
    store.initialize()
    store.upsert_detail(
        notice_id="100",
        title="通知",
        category="教学运行",
        published_date="2026-09-15",
        source_url="https://jwc.xjtu.edu.cn/info/1033/100.htm",
        body_text="旧正文",
        body_html="<p>旧正文</p>",
        fetched_at="2026-09-17T00:00:00Z",
    )
    old = store.get_notice("100")
    run_id = store.begin_sync_run(started_at="2026-09-17T01:00:00Z")
    store.upsert_detail(
        notice_id="100",
        title="通知更正",
        category="教学运行",
        published_date="2026-09-16",
        source_url="https://jwc.xjtu.edu.cn/info/1033/100.htm",
        body_text="",
        body_html="",
        fetched_at="2026-09-17T01:00:00Z",
        run_id=run_id,
    )

    refreshed = store.get_notice("100")
    assert refreshed["body_text"] == "旧正文"
    assert refreshed["body_html"] == "<p>旧正文</p>"
    assert refreshed["content_hash"] == old["content_hash"]
    assert refreshed["fetch_status"] == "failed"
    with store._connection() as connection:
        failure = connection.execute(
            "SELECT error_message FROM sync_failures WHERE notice_id = '100'"
        ).fetchone()
    assert failure is not None
    assert "empty" in failure["error_message"]

    store.upsert_detail(
        notice_id="101",
        title="全新空通知",
        category="教学运行",
        published_date="2026-09-16",
        source_url="https://jwc.xjtu.edu.cn/info/1033/101.htm",
        body_text="",
        body_html="",
        fetched_at="2026-09-17T01:00:00Z",
    )
    assert store.get_notice("101")["fetch_status"] == "empty_body"


def test_sync_runs_and_known_metadata_are_recorded(tmp_path) -> None:
    store = NoticeStore(tmp_path / "notices.sqlite3")
    store.initialize()
    run_id = store.begin_sync_run(started_at="2026-09-17T00:00:00Z")
    store.upsert_detail(
        notice_id="1",
        title="通知",
        category="教学运行",
        published_date="2026-09-15",
        source_url="https://jwc.xjtu.edu.cn/info/1033/1.htm",
        body_text="正文",
        body_html="<p>正文</p>",
        fetched_at="2026-09-17T00:00:00Z",
        run_id=run_id,
    )
    store.finish_sync_run(
        run_id, status="completed", stats={"processed": 1}, finished_at="2026-09-17T01:00:00Z"
    )

    known = store.get_known_notice_metadata("1")
    assert known["content_hash"] == store.get_notice("1")["content_hash"]
    assert store.sync_status()["last_run"]["status"] == "completed"


def test_integrity_keeps_a_title_only_fts_row_for_a_failed_new_detail(tmp_path) -> None:
    store = NoticeStore(tmp_path / "notices.sqlite3")
    store.initialize()
    store.mark_seen(
        notice_id="broken",
        title="暂时无法获取的通知",
        category="教学运行",
        published_date="2026-09-15",
        source_url="https://jwc.xjtu.edu.cn/info/1033/broken.htm",
        seen_at="2026-09-17T00:00:00Z",
    )
    store.record_detail_failure(
        "broken", "HTTP 503", run_id=None, occurred_at="2026-09-17T00:00:01Z"
    )

    assert store.integrity_check()["ok"] is True
    assert store.search(query="暂时无法获取")["items"][0]["notice_id"] == "broken"


def test_store_connection_context_always_closes_connection(tmp_path) -> None:
    store = NoticeStore(tmp_path / "notices.sqlite3")
    connection = store._connect()
    closed = False

    class ConnectionProxy:
        def __enter__(self):
            return connection

        def __exit__(self, *args):
            return False

        def close(self):
            nonlocal closed
            closed = True
            connection.close()

    store._connect = lambda: ConnectionProxy()  # type: ignore[method-assign]

    with store._connection():
        pass

    assert closed is True
