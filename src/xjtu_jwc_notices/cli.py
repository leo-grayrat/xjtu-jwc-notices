"""Command-line interface for synchronization and local maintenance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .paths import AppPaths
from .scheduler import TaskScheduler
from .session import XjtuSiteSession
from .storage import NoticeStore
from .sync import NoticeSyncService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xjtu-notices")
    parser.add_argument("--data-dir", type=Path, help="override the local application data path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="synchronize notices from the source website")
    commands.add_parser("status", help="show local archive and last-run status")
    commands.add_parser("check", help="check SQLite and full-text-index integrity")
    schedule = commands.add_parser("schedule", help="manage the daily Windows task")
    schedule_commands = schedule.add_subparsers(dest="schedule_command", required=True)
    install = schedule_commands.add_parser("install", help="install or replace the daily task")
    install.add_argument("--time", default="08:00", help="daily 24-hour time, HH:MM")
    schedule_commands.add_parser("status", help="query the task")
    schedule_commands.add_parser("uninstall", help="remove the task")
    return parser


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _schedule(args: argparse.Namespace) -> int:
    scheduler = TaskScheduler()
    try:
        if args.schedule_command == "install":
            result = scheduler.install(args.time)
        elif args.schedule_command == "status":
            result = scheduler.status()
        else:
            result = scheduler.uninstall()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return 0 if result.returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "schedule":
        return _schedule(args)
    paths = AppPaths.from_data_dir(args.data_dir) if args.data_dir else AppPaths.default()
    store = NoticeStore(paths.database)
    try:
        if args.command == "sync":
            with XjtuSiteSession(cookie_file=paths.cookies) as session:
                result = NoticeSyncService(session, store).sync()
        elif args.command == "status":
            store.initialize()
            result = store.sync_status()
        else:
            store.initialize()
            result = store.integrity_check()
        _print_json(result)
        if args.command == "check" and not result["ok"]:
            return 1
        return 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

