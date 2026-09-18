import subprocess
import sys

import pytest

from xjtu_jwc_notices.scheduler import TASK_NAME, TaskScheduler


def test_scheduler_installs_daily_task_with_current_python():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="SUCCESS", stderr="")

    scheduler = TaskScheduler(runner=runner, python_executable=sys.executable)
    result = scheduler.install("08:00")

    assert result.returncode == 0
    assert calls == [
        (
            [
                "schtasks.exe",
                "/Create",
                "/TN",
                TASK_NAME,
                "/TR",
                f'"{sys.executable}" -m xjtu_jwc_notices.cli sync',
                "/SC",
                "DAILY",
                "/ST",
                "08:00",
                "/F",
            ],
            {"check": False, "capture_output": True, "text": True},
        )
    ]


@pytest.mark.parametrize("value", ["8:00", "24:00", "12:60", "tomorrow"])
def test_scheduler_rejects_invalid_time_before_running(value):
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True

    scheduler = TaskScheduler(runner=runner)

    with pytest.raises(ValueError, match="HH:MM"):
        scheduler.install(value)
    assert called is False


def test_scheduler_queries_and_uninstalls_named_task():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="OK", stderr="")

    scheduler = TaskScheduler(runner=runner)
    scheduler.status()
    scheduler.uninstall()

    assert calls == [
        ["schtasks.exe", "/Query", "/TN", TASK_NAME, "/FO", "LIST"],
        ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"],
    ]

