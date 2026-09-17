"""Fixture-friendly parsers for XJTU teaching-notice pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Final
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag


class ParseError(ValueError):
    """Raised when an expected notice-page structure is absent."""


class NonContentPageError(ParseError):
    """Raised for access-control and JavaScript challenge pages."""


_DATE_PATTERN: Final = re.compile(
    r"(?:发布时间|发布日期|发布(?:日期|时间)?)?\s*[：:]?\s*"
    r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?"
)
_MODERN_ID_PATTERN: Final = re.compile(r"/info/[^/]+/(\d+)\.htm(?:$|[?#])", re.I)
_NEXT_TEXT_PATTERN: Final = re.compile(r"^(?:下一页|下页|next(?:\s*page)?)$", re.I)
_CHALLENGE_MARKER: Final = re.compile(r"\bchallenge_id\b", re.I)
_BRACKETED_CATEGORY: Final = re.compile(r"^\s*\[([^\]]+)\]\s*(.+)$")


@dataclass(frozen=True)
class NoticeListEntry:
    title: str
    category: str
    published_date: date
    url: str


@dataclass(frozen=True)
class NoticeListPage:
    entries: tuple[NoticeListEntry, ...]
    next_page_url: str | None


@dataclass(frozen=True)
class NoticeDetail:
    notice_id: str
    heading: str
    category: str | None
    published_date: date | None
    body_text: str
    body_html: str
    is_empty: bool


def _normalized_text(element: Tag) -> str:
    return " ".join(element.get_text(" ", strip=True).split())


def _parse_date(text: str) -> date | None:
    match = _DATE_PATTERN.search(text)
    if not match:
        return None
    return date(*(int(part) for part in match.groups()))


def _category_from(element: Tag) -> str | None:
    category = element.select_one(".category, .type, [data-category]")
    if category:
        value = category.get("data-category") or _normalized_text(category)
        if value:
            return value
    text = _normalized_text(element)
    match = re.search(r"(?:分类|栏目|所属分类)\s*[：:]\s*([^\s|｜]+)", text)
    return match.group(1) if match else None


def _breadcrumb_category(soup: BeautifulSoup) -> str | None:
    breadcrumb = soup.select_one(".breadcrumb, .position, .location")
    if breadcrumb is None:
        return None
    links = breadcrumb.select("a")
    return _normalized_text(links[-1]) if links else None


def is_non_content_page(html: str) -> bool:
    """Return whether *html* is an access-control or challenge response."""

    lowered = html.lower()
    return (
        "access denied" in lowered
        or "you don't have permission" in lowered
        or "您无权访问" in html
        or "拒绝访问" in html
        or bool(_CHALLENGE_MARKER.search(html))
    )


def extract_notice_id(url: str, html: str = "") -> str:
    """Extract the stable notice ID from modern paths or legacy markup."""

    modern = _MODERN_ID_PATTERN.search(urlparse(url).path)
    if modern:
        return modern.group(1)

    query_id = parse_qs(urlparse(url).query).get("wbnewsid", [])
    if query_id and query_id[0]:
        return query_id[0]

    if html:
        soup = BeautifulSoup(html, "html.parser")
        field = soup.select_one('input[name="wbnewsid"]')
        if field and field.get("value"):
            return str(field["value"])
    raise ParseError(f"could not find a stable notice ID in {url!r}")


def parse_list(html: str, page_url: str) -> NoticeListPage:
    """Parse a ``jxtz2`` list response and its discovered next page."""

    if is_non_content_page(html):
        raise NonContentPageError("list response is an access-control or challenge page")
    soup = BeautifulSoup(html, "html.parser")
    entries: list[NoticeListEntry] = []
    for item in soup.select("li"):
        link = item.select_one('a[href*="/info/"], a[href*="info/"], a[href*="content.jsp"]')
        if link is None:
            continue
        title = _normalized_text(link)
        category = _category_from(item)
        bracketed_category = _BRACKETED_CATEGORY.match(title)
        if bracketed_category:
            category = category or bracketed_category.group(1)
            title = bracketed_category.group(2)
        published_date = _parse_date(_normalized_text(item))
        href = link.get("href")
        if not title or not category or not published_date or not href:
            raise ParseError("notice list entry is missing title, category, date, or URL")
        entries.append(
            NoticeListEntry(
                title=title,
                category=category,
                published_date=published_date,
                url=urljoin(page_url, href),
            )
        )

    next_page_url = None
    for link in soup.select("a[href]"):
        if link.get("rel") == ["next"] or _NEXT_TEXT_PATTERN.match(_normalized_text(link)):
            next_page_url = urljoin(page_url, str(link["href"]))
            break
    return NoticeListPage(entries=tuple(entries), next_page_url=next_page_url)


def _body_text(body: Tag) -> str:
    parts: list[str] = []
    for child in body.children:
        if isinstance(child, NavigableString):
            text = " ".join(str(child).split())
        elif isinstance(child, Tag):
            text = _normalized_text(child)
        else:
            continue
        if text:
            parts.append(text)
    return "\n".join(parts)


def parse_detail(html: str, url: str) -> NoticeDetail:
    """Extract one notice body, retaining both reader text and source HTML."""

    if is_non_content_page(html):
        raise NonContentPageError("detail response is an access-control or challenge page")
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(
        "#vsb_content, [id^=\"vsb_content_\"], .v_news_content, .article-content, .content"
    )
    if body is None:
        raise ParseError("notice detail has no recognized content container")
    heading_element = soup.select_one("h1, h3, .title, .article-title")
    if heading_element is None or not (heading := _normalized_text(heading_element)):
        raise ParseError("notice detail has no heading")

    metadata = soup.select_one(".meta, .article-meta, .info")
    metadata_text = _normalized_text(metadata) if metadata else _normalized_text(soup)
    body_text = _body_text(body)
    return NoticeDetail(
        notice_id=extract_notice_id(url, html),
        heading=heading,
        category=(
            (_category_from(metadata) if metadata else None)
            or _breadcrumb_category(soup)
            or _category_from(soup)
        ),
        published_date=_parse_date(metadata_text),
        body_text=body_text,
        body_html="".join(str(child) for child in body.contents).strip(),
        is_empty=not body_text,
    )
