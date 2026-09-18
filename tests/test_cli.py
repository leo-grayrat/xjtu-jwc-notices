import json
import subprocess

from xjtu_jwc_notices import cli
from xjtu_jwc_notices.paths import AppPaths
from xjtu_jwc_notices.storage import NoticeStore


def test_status_command_prints_json_from_local_database(tmp_path, capsys):
    paths = AppPaths(tmp_path, tmp_path / "notices.db", tmp_path / "cookies.json")
    store = NoticeStore(paths.database)
    store.initialize()

    exit_code = cli.main(["--data-dir", str(tmp_path), "status"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["notice_count"] == 0


def test_check_command_returns_nonzero_for_failed_integrity(monkeypatch, tmp_path, capsys):
    class BrokenStore:
        def __init__(self, path):
            pass

        def initialize(self):
            pass

        def integrity_check(self):
            return {"ok": False, "quick_check": "corrupt"}

    monkeypatch.setattr(cli, "NoticeStore", BrokenStore)

    exit_code = cli.main(["--data-dir", str(tmp_path), "check"])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_sync_command_closes_session_and_prints_stats(monkeypatch, tmp_path, capsys):
    events = []

    class FakeSession:
        def __init__(self, **kwargs):
            events.append(("session", kwargs["cookie_file"]))

        def __enter__(self):
            events.append(("enter",))
            return self

        def __exit__(self, *args):
            events.append(("exit",))

    class FakeService:
        def __init__(self, session, store):
            events.append(("service", type(session).__name__, type(store).__name__))

        def sync(self):
            return {"pages": 1, "ok": 2}

    monkeypatch.setattr(cli, "XjtuSiteSession", FakeSession)
    monkeypatch.setattr(cli, "NoticeSyncService", FakeService)

    exit_code = cli.main(["--data-dir", str(tmp_path), "sync"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"ok": 2, "pages": 1}
    assert events[-1] == ("exit",)


def test_schedule_install_propagates_subprocess_failure(monkeypatch, capsys):
    class FailedScheduler:
        def install(self, time):
            assert time == "08:00"
            return subprocess.CompletedProcess([], 1, stdout="", stderr="denied")

    monkeypatch.setattr(cli, "TaskScheduler", FailedScheduler)

    exit_code = cli.main(["schedule", "install"])

    assert exit_code == 1
    assert "denied" in capsys.readouterr().err

