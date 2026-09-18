"""Windows Task Scheduler integration."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Sequence

TASK_NAME = "XJTU JWC Notices Sync"
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class TaskScheduler:
    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        python_executable: str = sys.executable,
    ) -> None:
        self.runner = runner
        self.python_executable = python_executable

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(list(arguments), check=False, capture_output=True, text=True)

    def install(self, time: str = "08:00") -> subprocess.CompletedProcess[str]:
        if not _TIME_PATTERN.fullmatch(time):
            raise ValueError("time must use 24-hour HH:MM format")
        action = f'"{self.python_executable}" -m xjtu_jwc_notices.cli sync'
        return self._run(
            [
                "schtasks.exe",
                "/Create",
                "/TN",
                TASK_NAME,
                "/TR",
                action,
                "/SC",
                "DAILY",
                "/ST",
                time,
                "/F",
            ]
        )

    def status(self) -> subprocess.CompletedProcess[str]:
        return self._run(["schtasks.exe", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])

    def uninstall(self) -> subprocess.CompletedProcess[str]:
        return self._run(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"])

