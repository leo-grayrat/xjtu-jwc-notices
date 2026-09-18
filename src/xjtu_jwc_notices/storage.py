"""SQLite-backed local notice archive and full-text search."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 1


def utc_now() -> str:
    """Return an unambiguous UTC timestamp suitable for stored metadata."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonicalize_url(url: str) -> str:
    """Normalize a notice URL while retaining its meaningful query parameters."""

    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def _date_value(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else value


def _cursor_encode(published_date: str, notice_id: str) -> str:
    payload = json.dumps([published_date, notice_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _cursor_decode(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        published_date, notice_id = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid search cursor") from error
    if not isinstance(published_date, str) or not isinstance(notice_id, str):
        raise ValueError("invalid search cursor")
    return published_date, notice_id


class NoticeStore:
    """A small transactional archive, designed for item-by-item resumable syncs."""

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def validate_schema(self) -> None:
        """Validate an existing archive without changing it."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()
        except sqlite3.Error as error:
            raise RuntimeError(f"notice database is unavailable or invalid: {error}") from error
        if row is None or row[0] != SCHEMA_VERSION:
            version = None if row is None else row[0]
            raise RuntimeError(f"unsupported database schema version {version}")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notices (
                    notice_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    published_date TEXT NOT NULL,
                    body_text TEXT,
                    body_html TEXT,
                    source_url TEXT NOT NULL,
                    content_hash TEXT,
                    fetch_status TEXT NOT NULL
                        CHECK(fetch_status IN ('ok', 'empty_body', 'failed')),
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    fetched_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS notices_published_idx
                    ON notices(published_date DESC, notice_id DESC);
                CREATE INDEX IF NOT EXISTS notices_category_idx ON notices(category);
                CREATE TABLE IF NOT EXISTS sync_runs (
                    run_id INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL
                        CHECK(status IN ('running', 'completed', 'failed', 'interrupted')),
                    stats_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT
                );
                CREATE TABLE IF NOT EXISTS sync_failures (
                    failure_id INTEGER PRIMARY KEY,
                    run_id INTEGER REFERENCES sync_runs(run_id),
                    notice_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    error_message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sync_failures_notice_idx ON sync_failures(notice_id);
                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS notices_fts USING fts5(
                    notice_id UNINDEXED, title, body_text
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )
            version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported database schema version {version}")

    def get_known_notice_metadata(self, notice_id: str) -> dict[str, object] | None:
        return self._row(
            """SELECT notice_id, title, category, published_date, source_url, content_hash,
                      fetch_status, fetched_at, last_seen_at
               FROM notices WHERE notice_id = ?""",
            (notice_id,),
        )

    def list_known_notice_metadata(self) -> list[dict[str, object]]:
        return self._rows(
            """SELECT notice_id, title, category, published_date, source_url, content_hash,
                      fetch_status, fetched_at, last_seen_at
               FROM notices ORDER BY published_date DESC, notice_id DESC"""
        )

    def initial_backfill_complete(self) -> bool:
        state = self._row("SELECT value FROM sync_state WHERE key = 'initial_backfill_complete'")
        return state is not None and state["value"] == "1"

    def mark_seen(
        self,
        *,
        notice_id: str,
        title: str,
        category: str,
        published_date: date | str,
        source_url: str,
        seen_at: str | None = None,
        needs_fetch: bool = False,
    ) -> None:
        seen_at = seen_at or utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO notices(
                    notice_id, title, category, published_date, body_text, body_html,
                    source_url, content_hash, fetch_status, first_seen_at, last_seen_at,
                    fetched_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?, NULL, 'failed', ?, ?, NULL, ?)
                ON CONFLICT(notice_id) DO UPDATE SET
                    title=excluded.title, category=excluded.category,
                    published_date=excluded.published_date, source_url=excluded.source_url,
                    fetch_status=CASE WHEN ? THEN 'failed' ELSE notices.fetch_status END,
                    last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at
                """,
                (
                    notice_id,
                    title,
                    category,
                    _date_value(published_date),
                    canonicalize_url(source_url),
                    seen_at,
                    seen_at,
                    seen_at,
                    needs_fetch,
                ),
            )
            body_row = connection.execute(
                "SELECT body_text FROM notices WHERE notice_id = ?", (notice_id,)
            ).fetchone()
            connection.execute("DELETE FROM notices_fts WHERE notice_id = ?", (notice_id,))
            connection.execute(
                "INSERT INTO notices_fts(notice_id, title, body_text) VALUES (?, ?, ?)",
                (notice_id, title, body_row["body_text"] if body_row is not None else ""),
            )

    def upsert_detail(
        self,
        *,
        notice_id: str,
        title: str,
        category: str,
        published_date: date | str,
        source_url: str,
        body_text: str,
        body_html: str,
        fetched_at: str | None = None,
        run_id: int | None = None,
    ) -> None:
        """Store one fetched detail atomically, including its FTS document."""

        fetched_at = fetched_at or utc_now()
        body_text = body_text.strip()
        status = "ok" if body_text else "empty_body"
        content_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        source_url = canonicalize_url(source_url)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT body_text FROM notices WHERE notice_id = ?", (notice_id,)
            ).fetchone()
            if not body_text and existing is not None and str(existing["body_text"] or "").strip():
                connection.execute(
                    """UPDATE notices SET title=?, category=?, published_date=?, source_url=?,
                       fetch_status='failed', last_seen_at=?, updated_at=? WHERE notice_id=?""",
                    (
                        title,
                        category,
                        _date_value(published_date),
                        source_url,
                        fetched_at,
                        fetched_at,
                        notice_id,
                    ),
                )
                connection.execute("DELETE FROM notices_fts WHERE notice_id = ?", (notice_id,))
                connection.execute(
                    "INSERT INTO notices_fts(notice_id, title, body_text) VALUES (?, ?, ?)",
                    (notice_id, title, existing["body_text"]),
                )
                connection.execute(
                    """INSERT INTO sync_failures(run_id, notice_id, occurred_at, error_message)
                       VALUES (?, ?, ?, ?)""",
                    (run_id, notice_id, fetched_at, "empty detail body; retained existing archive"),
                )
                return
            connection.execute(
                """
                INSERT INTO notices(
                    notice_id, title, category, published_date, body_text, body_html,
                    source_url, content_hash, fetch_status, first_seen_at, last_seen_at,
                    fetched_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(notice_id) DO UPDATE SET
                    title=excluded.title, category=excluded.category,
                    published_date=excluded.published_date, body_text=excluded.body_text,
                    body_html=excluded.body_html, source_url=excluded.source_url,
                    content_hash=excluded.content_hash, fetch_status=excluded.fetch_status,
                    last_seen_at=excluded.last_seen_at, fetched_at=excluded.fetched_at,
                    updated_at=excluded.updated_at
                """,
                (
                    notice_id,
                    title,
                    category,
                    _date_value(published_date),
                    body_text,
                    body_html,
                    source_url,
                    content_hash,
                    status,
                    fetched_at,
                    fetched_at,
                    fetched_at,
                    fetched_at,
                ),
            )
            connection.execute("DELETE FROM notices_fts WHERE notice_id = ?", (notice_id,))
            connection.execute(
                "INSERT INTO notices_fts(notice_id, title, body_text) VALUES (?, ?, ?)",
                (notice_id, title, body_text),
            )

    def record_detail_failure(
        self,
        notice_id: str,
        error_message: str,
        *,
        run_id: int | None,
        occurred_at: str | None = None,
    ) -> None:
        occurred_at = occurred_at or utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE notices SET fetch_status='failed', updated_at=? WHERE notice_id=?",
                (occurred_at, notice_id),
            )
            connection.execute(
                """INSERT INTO sync_failures(run_id, notice_id, occurred_at, error_message)
                   VALUES (?, ?, ?, ?)""",
                (run_id, notice_id, occurred_at, error_message),
            )

    def begin_sync_run(self, *, started_at: str | None = None) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO sync_runs(started_at, status) VALUES (?, 'running')",
                (started_at or utc_now(),),
            )
            return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        stats: Mapping[str, object],
        finished_at: str | None = None,
        error_message: str | None = None,
        backfill_completed: bool = False,
    ) -> None:
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError("invalid final run status")
        with self._connect() as connection:
            connection.execute(
                """UPDATE sync_runs SET finished_at=?, status=?, stats_json=?, error_message=?
                   WHERE run_id=?""",
                (finished_at or utc_now(), status, json.dumps(dict(stats)), error_message, run_id),
            )
            if backfill_completed:
                if status != "completed":
                    raise ValueError("only a completed run can complete initial backfill")
                connection.execute(
                    """INSERT INTO sync_state(key, value) VALUES ('initial_backfill_complete', '1')
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value"""
                )

    def get_notice(self, notice_id: str) -> dict[str, object] | None:
        return self._row("SELECT * FROM notices WHERE notice_id = ?", (notice_id,))

    def search(
        self,
        query: str | None = None,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        categories: Iterable[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, object]:
        limit = max(1, min(limit, 50))
        clauses: list[str] = []
        parameters: list[object] = []
        joins = ""
        snippet = "'' AS snippet"
        if query and query.strip():
            clauses.append(
                "(notices.notice_id IN (SELECT notice_id FROM notices_fts "
                "WHERE notices_fts MATCH ?) "
                "OR notices.title LIKE ? OR notices.body_text LIKE ?)"
            )
            literal_query = query.strip()
            fts_phrase = '"' + literal_query.replace('"', '""') + '"'
            parameters.extend((fts_phrase, f"%{literal_query}%", f"%{literal_query}%"))
            snippet = "substr(COALESCE(notices.body_text, notices.title), 1, 240) AS snippet"
        if date_from:
            clauses.append("notices.published_date >= ?")
            parameters.append(date_from)
        if date_to:
            clauses.append("notices.published_date <= ?")
            parameters.append(date_to)
        category_list = list(categories or [])
        if category_list:
            clauses.append("notices.category IN (" + ",".join("?" for _ in category_list) + ")")
            parameters.extend(category_list)
        if cursor:
            cursor_date, cursor_id = _cursor_decode(cursor)
            clauses.append(
                "(notices.published_date < ? OR "
                "(notices.published_date = ? AND notices.notice_id < ?))"
            )
            parameters.extend((cursor_date, cursor_date, cursor_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = (
            "SELECT notices.notice_id, notices.title, notices.category, notices.published_date, "
            "notices.source_url, notices.fetch_status, notices.fetched_at, "
            + snippet
            + " FROM notices"
            + joins
            + where
            + " ORDER BY notices.published_date DESC, notices.notice_id DESC LIMIT ?"
        )
        rows = self._rows(sql, (*parameters, limit + 1))
        next_cursor = None
        if len(rows) > limit:
            rows.pop()
            row = rows[-1]
            next_cursor = _cursor_encode(str(row["published_date"]), str(row["notice_id"]))
        data_as_of = self._row("SELECT MAX(last_seen_at) AS data_as_of FROM notices")
        return {
            "items": rows,
            "next_cursor": next_cursor,
            "data_as_of": data_as_of["data_as_of"] if data_as_of else None,
        }

    def list_categories(self) -> list[dict[str, object]]:
        return self._rows(
            """SELECT category, COUNT(*) AS count, MIN(published_date) AS oldest_date,
                      MAX(published_date) AS newest_date
               FROM notices GROUP BY category ORDER BY category"""
        )

    def sync_status(self) -> dict[str, object]:
        last_run = self._row("SELECT * FROM sync_runs ORDER BY run_id DESC LIMIT 1")
        if last_run is not None:
            last_run["stats"] = json.loads(str(last_run.pop("stats_json")))
        count = self._row("SELECT COUNT(*) AS count FROM notices")
        return {"notice_count": count["count"] if count else 0, "last_run": last_run}

    def integrity_check(self) -> dict[str, object]:
        with self._connect() as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
            notices_count = connection.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
            fts_count = connection.execute("SELECT COUNT(*) FROM notices_fts").fetchone()[0]
            missing = connection.execute(
                """SELECT COUNT(*) FROM notices n LEFT JOIN notices_fts f
                   ON f.notice_id=n.notice_id WHERE f.notice_id IS NULL"""
            ).fetchone()[0]
        return {
            "ok": quick_check == "ok" and notices_count == fts_count and missing == 0,
            "quick_check": quick_check,
            "notice_count": notices_count,
            "fts_count": fts_count,
            "missing_fts_count": missing,
        }

    def _row(self, sql: str, parameters: tuple[object, ...] = ()) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return dict(row) if row is not None else None

    def _rows(self, sql: str, parameters: tuple[object, ...] = ()) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]
