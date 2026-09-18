"""Sequential, resumable synchronization from the public notice list."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from .parsers import NoticeListEntry, extract_notice_id, parse_detail, parse_list
from .storage import NoticeStore, canonicalize_url, utc_now

DEFAULT_LIST_URL = "https://jwc.xjtu.edu.cn/jxxx/jxtz2.htm"
DEFAULT_CUTOFF = date(2026, 1, 1)


class Response(Protocol):
    status_code: int
    text: str


class SiteSession(Protocol):
    def get(self, url: str) -> Response: ...


class SyncError(RuntimeError):
    """Raised when a list page cannot be safely synchronized."""


class NoticeSyncService:
    """Sync a site session into :class:`NoticeStore` one detail at a time."""

    def __init__(
        self,
        session: SiteSession,
        store: NoticeStore,
        *,
        list_url: str = DEFAULT_LIST_URL,
        cutoff: date | str = DEFAULT_CUTOFF,
        refresh_after_days: int | None = 7,
        now: Callable[[], str] = utc_now,
    ) -> None:
        if refresh_after_days is not None and refresh_after_days < 0:
            raise ValueError("refresh_after_days must be non-negative or None")
        self.session = session
        self.store = store
        self.list_url = canonicalize_url(list_url)
        self.cutoff = date.fromisoformat(cutoff) if isinstance(cutoff, str) else cutoff
        self.refresh_after_days = refresh_after_days
        self.now = now

    def sync(self) -> dict[str, int]:
        """Synchronize only discovered list pages and return concrete run statistics."""

        self.store.initialize()
        initial_run = not self.store.initial_backfill_complete()
        run_id = self.store.begin_sync_run(started_at=self.now())
        stats = {"pages": 0, "processed": 0, "ok": 0, "empty": 0, "failed": 0, "skipped": 0}
        current_url: str | None = self.list_url
        seen_pages: set[str] = set()
        attempted_ids: set[str] = set()
        quiet_pages = 0
        try:
            if not initial_run:
                for known in self.store.list_known_notice_metadata():
                    retry_due = str(known["fetch_status"]) in {
                        "failed",
                        "empty_body",
                    } or self._is_stale(known.get("fetched_at"))
                    published_date = date.fromisoformat(str(known["published_date"]))
                    if not retry_due or published_date < self.cutoff:
                        continue
                    notice_id = str(known["notice_id"])
                    attempted_ids.add(notice_id)
                    self._sync_entry(
                        NoticeListEntry(
                            title=str(known["title"]),
                            category=str(known["category"]),
                            published_date=published_date,
                            url=str(known["source_url"]),
                        ),
                        notice_id,
                        run_id,
                        stats,
                    )
            while current_url is not None and current_url not in seen_pages:
                seen_pages.add(current_url)
                page = self._get_list_page(current_url)
                stats["pages"] += 1
                entries = [entry for entry in page.entries if entry.published_date >= self.cutoff]
                page_actionable = False
                for entry in entries:
                    notice_id = extract_notice_id(entry.url)
                    known = self.store.get_known_notice_metadata(notice_id)
                    if self._actionable(entry, known) and notice_id not in attempted_ids:
                        page_actionable = True
                        attempted_ids.add(notice_id)
                        self._sync_entry(entry, notice_id, run_id, stats)
                    else:
                        self.store.mark_seen(
                            notice_id=notice_id,
                            title=entry.title,
                            category=entry.category,
                            published_date=entry.published_date,
                            source_url=entry.url,
                            seen_at=self.now(),
                        )
                        stats["skipped"] += 1
                if initial_run:
                    quiet_pages = quiet_pages + 1 if not entries else 0
                else:
                    quiet_pages = quiet_pages + 1 if not page_actionable else 0
                if quiet_pages >= 2:
                    break
                current_url = page.next_page_url
            run_status = "failed" if stats["failed"] else "completed"
            self.store.finish_sync_run(
                run_id,
                status=run_status,
                stats=stats,
                finished_at=self.now(),
                error_message=(
                    f'{stats["failed"]} notice detail request(s) failed'
                    if stats["failed"]
                    else None
                ),
                backfill_completed=initial_run and not stats["failed"],
            )
            return stats
        except KeyboardInterrupt:
            self.store.finish_sync_run(
                run_id, status="interrupted", stats=stats, finished_at=self.now()
            )
            raise
        except Exception as error:
            self.store.finish_sync_run(
                run_id,
                status="failed",
                stats=stats,
                finished_at=self.now(),
                error_message=str(error),
            )
            raise

    def _get_list_page(self, url: str):
        response = self.session.get(url)
        if not 200 <= response.status_code < 300:
            raise SyncError(f"list request {url} returned HTTP {response.status_code}")
        return parse_list(response.text, url)

    def _actionable(self, entry: NoticeListEntry, known: dict[str, object] | None) -> bool:
        if known is None:
            return True
        if str(known["fetch_status"]) in {"failed", "empty_body"}:
            return True
        if any(
            (
                str(known[field]) != value
                for field, value in (
                    ("title", entry.title),
                    ("category", entry.category),
                    ("published_date", entry.published_date.isoformat()),
                    ("source_url", canonicalize_url(entry.url)),
                )
            )
        ):
            return True
        return self._is_stale(known.get("fetched_at"))

    def _is_stale(self, fetched_at: object) -> bool:
        if self.refresh_after_days is None:
            return False
        if not isinstance(fetched_at, str):
            return True
        try:
            fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            now = datetime.fromisoformat(self.now().replace("Z", "+00:00"))
        except ValueError:
            return True
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now - fetched >= timedelta(days=self.refresh_after_days)

    def _sync_entry(
        self,
        entry: NoticeListEntry,
        notice_id: str,
        run_id: int,
        stats: dict[str, int],
    ) -> None:
        seen_at = self.now()
        self.store.mark_seen(
            notice_id=notice_id,
            title=entry.title,
            category=entry.category,
            published_date=entry.published_date,
            source_url=entry.url,
            seen_at=seen_at,
            needs_fetch=True,
        )
        stats["processed"] += 1
        try:
            response = self.session.get(entry.url)
            if not 200 <= response.status_code < 300:
                raise SyncError(f"detail request {entry.url} returned HTTP {response.status_code}")
            detail = parse_detail(response.text, entry.url)
            if detail.notice_id != notice_id:
                raise SyncError(f"detail ID {detail.notice_id} does not match list ID {notice_id}")
            body_text = detail.body_text
            saved_status = self.store.upsert_detail(
                notice_id=notice_id,
                title=detail.heading or entry.title,
                category=detail.category or entry.category,
                published_date=detail.published_date or entry.published_date,
                source_url=entry.url,
                body_text=body_text,
                body_html=detail.body_html,
                fetched_at=self.now(),
                run_id=run_id,
            )
            stats[
                "failed"
                if saved_status == "failed"
                else "empty"
                if saved_status == "empty_body"
                else "ok"
            ] += 1
        except KeyboardInterrupt:
            raise
        except Exception as error:
            self.store.record_detail_failure(
                notice_id,
                str(error),
                run_id=run_id,
                occurred_at=self.now(),
            )
            stats["failed"] += 1
