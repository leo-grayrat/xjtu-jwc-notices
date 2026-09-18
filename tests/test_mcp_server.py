import asyncio
from datetime import date

from xjtu_jwc_notices.mcp_server import NoticeMcpService, mcp
from xjtu_jwc_notices.storage import NoticeStore


def populated_store(tmp_path):
    store = NoticeStore(tmp_path / "notices.db")
    store.initialize()
    store.upsert_detail(
        notice_id="10463",
        title="FPGA 创新设计赛道报名通知",
        category="竞赛安排",
        published_date=date(2026, 9, 15),
        source_url="https://jwc.xjtu.edu.cn/info/1172/10463.htm",
        body_text="报名截止日期为九月二十二日。",
        body_html="<p>报名截止日期为九月二十二日。</p>",
        fetched_at="2026-09-18T00:00:00Z",
    )
    run_id = store.begin_sync_run(started_at="2026-09-18T00:00:00Z")
    store.finish_sync_run(
        run_id,
        status="completed",
        stats={"ok": 1},
        finished_at="2026-09-18T00:01:00Z",
    )
    return store


def test_mcp_search_then_get_returns_source_and_freshness(tmp_path):
    service = NoticeMcpService(populated_store(tmp_path))

    search = service.search_notices(query="FPGA", limit=20)
    detail = service.get_notice("10463")

    assert search["items"][0]["notice_id"] == "10463"
    assert search["items"][0]["source_url"].startswith("https://jwc.xjtu.edu.cn/")
    assert search["data_as_of"] == "2026-09-18T00:00:00Z"
    assert detail["found"] is True
    assert detail["notice"]["body_text"] == "报名截止日期为九月二十二日。"
    assert detail["data_as_of"] == "2026-09-18T00:00:00Z"


def test_mcp_list_categories_and_sync_status_are_local(tmp_path):
    service = NoticeMcpService(populated_store(tmp_path))

    categories = service.list_categories()
    status = service.get_sync_status()

    assert categories["categories"][0]["category"] == "竞赛安排"
    assert categories["data_as_of"] == "2026-09-18T00:00:00Z"
    assert status["notice_count"] == 1
    assert status["last_run"]["status"] == "completed"


def test_mcp_get_missing_notice_is_structured(tmp_path):
    store = NoticeStore(tmp_path / "notices.db")
    store.initialize()
    service = NoticeMcpService(store)

    result = service.get_notice("missing")

    assert result == {"found": False, "notice_id": "missing", "data_as_of": None}


def test_stdio_server_registers_exactly_the_four_public_tools():
    tools = asyncio.run(mcp.list_tools())

    assert {tool.name for tool in tools} == {
        "search_notices",
        "get_notice",
        "list_categories",
        "get_sync_status",
    }
