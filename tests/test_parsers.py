from pathlib import Path

import pytest

from xjtu_jwc_notices.parsers import (
    NonContentPageError,
    extract_notice_id,
    parse_detail,
    parse_list,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_jxtz2_list_extracts_entries_and_next_page() -> None:
    page = parse_list(fixture("jxtz2_list.html"), "https://jwc.xjtu.edu.cn/jxtz2.htm")

    assert page.entries[0].title == "关于 2026 年秋季学期选课的通知"
    assert page.entries[0].category == "教学运行"
    assert page.entries[0].published_date.isoformat() == "2026-09-15"
    assert page.entries[0].url == "https://jwc.xjtu.edu.cn/info/1033/41234.htm"
    assert page.entries[1].published_date.isoformat() == "2026-09-14"
    assert page.next_page_url == "https://jwc.xjtu.edu.cn/jxtz2.htm?PAGENUM=2"


def test_parse_live_list_uses_bracketed_category_and_legacy_link() -> None:
    page = parse_list(fixture("live_jxtz2_list.html"), "https://jwc.xjtu.edu.cn/jxtz2.htm")

    assert page.entries[0].category == "竞赛安排"
    assert page.entries[0].title == "2026 年全国大学生竞赛报名通知"
    assert page.entries[0].url.endswith(
        "content.jsp?urltype=news.NewsContentUrl&wbtreeid=1033&wbnewsid=48888"
    )
    assert page.entries[1].category == "教学运行"
    assert page.entries[1].title == "秋季学期课程调整通知"


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        ("https://jwc.xjtu.edu.cn/info/1033/41234.htm", "", "41234"),
        (
            "https://jwc.xjtu.edu.cn/content.jsp?urltype=news.NewsContentUrl"
            "&wbtreeid=1033&wbnewsid=39876",
            fixture("legacy_detail.html"),
            "39876",
        ),
    ],
)
def test_extract_notice_id_from_modern_or_legacy_detail(url: str, html: str, expected: str) -> None:
    assert extract_notice_id(url, html) == expected


def test_parse_detail_preserves_text_and_html() -> None:
    detail = parse_detail(fixture("jxtz2_detail.html"), "https://jwc.xjtu.edu.cn/info/1033/41234.htm")

    assert detail.notice_id == "41234"
    assert detail.heading == "关于 2026 年秋季学期选课的通知"
    assert detail.category == "教学运行"
    assert detail.published_date.isoformat() == "2026-09-15"
    assert detail.body_text == "各学院（部）及全体同学：\n选课工作将于 9 月 20 日 开始。"
    assert "<strong>9 月 20 日</strong>" in detail.body_html
    assert not detail.is_empty


def test_parse_live_detail_supports_vsb_content_suffix_breadcrumb_and_all_body_text() -> None:
    detail = parse_detail(
        fixture("live_jxtz2_detail.html"),
        "https://jwc.xjtu.edu.cn/content.jsp?wbnewsid=48888",
    )

    assert detail.notice_id == "48888"
    assert detail.heading == "2026 年全国大学生竞赛报名通知"
    assert detail.category == "竞赛安排"
    assert detail.body_text == (
        "请各学院按时组织报名。\n报名截止日期：9 月 30 日\n竞赛联系人：张老师。\n逾期不再受理。"
    )


def test_parse_detail_marks_whitespace_only_body_empty() -> None:
    detail = parse_detail(fixture("empty_detail.html"), "https://jwc.xjtu.edu.cn/info/1033/40001.htm")

    assert detail.is_empty
    assert detail.body_text == ""


@pytest.mark.parametrize("name", ["access_denied.html", "dynamic_challenge.html"])
def test_parse_detail_rejects_non_content_pages(name: str) -> None:
    with pytest.raises(NonContentPageError):
        parse_detail(fixture(name), "https://jwc.xjtu.edu.cn/info/1033/40001.htm")
