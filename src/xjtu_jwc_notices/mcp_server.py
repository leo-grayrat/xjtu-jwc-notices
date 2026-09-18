"""Read-only stdio MCP server for the local notice archive."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .paths import AppPaths
from .storage import NoticeStore


class NoticeMcpService:
    """Pure local-query facade used by MCP tools."""

    def __init__(self, store: NoticeStore) -> None:
        self.store = store
        self.store.validate_schema()

    def _data_as_of(self) -> str | None:
        return self.store.search(limit=1)["data_as_of"]  # type: ignore[return-value]

    def search_notices(
        self,
        query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        categories: Iterable[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return self.store.search(
            query,
            date_from=date_from,
            date_to=date_to,
            categories=categories,
            limit=limit,
            cursor=cursor,
        )

    def get_notice(self, notice_id: str) -> dict[str, Any]:
        notice = self.store.get_notice(notice_id)
        data_as_of = self._data_as_of()
        if notice is None:
            return {"found": False, "notice_id": notice_id, "data_as_of": data_as_of}
        return {"found": True, "notice": notice, "data_as_of": data_as_of}

    def list_categories(self) -> dict[str, Any]:
        return {"categories": self.store.list_categories(), "data_as_of": self._data_as_of()}

    def get_sync_status(self) -> dict[str, Any]:
        return self.store.sync_status()


_service: NoticeMcpService | None = None
mcp = FastMCP("xjtu-jwc-notices")


def _default_service() -> NoticeMcpService:
    global _service
    if _service is None:
        _service = NoticeMcpService(NoticeStore(AppPaths.default().database, read_only=True))
    return _service


@mcp.tool()
def search_notices(
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    categories: list[str] | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Search locally archived notices; this never accesses the source website."""

    return _default_service().search_notices(
        query, date_from, date_to, categories, limit, cursor
    )


@mcp.tool()
def get_notice(notice_id: str) -> dict[str, Any]:
    """Return one locally archived notice and its original source URL."""

    return _default_service().get_notice(notice_id)


@mcp.tool()
def list_categories() -> dict[str, Any]:
    """List locally archived notice categories and their date ranges."""

    return _default_service().list_categories()


@mcp.tool()
def get_sync_status() -> dict[str, Any]:
    """Return local archive freshness and the most recent synchronization result."""

    return _default_service().get_sync_status()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
