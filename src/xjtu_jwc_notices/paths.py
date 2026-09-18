"""Application data paths."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    database: Path
    cookies: Path

    @classmethod
    def from_data_dir(cls, data_dir: Path | str) -> AppPaths:
        directory = Path(data_dir)
        return cls(directory, directory / "notices.db", directory / "cookies.json")

    @classmethod
    def default(cls, *, platform: str | None = None) -> AppPaths:
        current_platform = platform or sys.platform
        if current_platform == "win32" and os.environ.get("LOCALAPPDATA"):
            root = Path(os.environ["LOCALAPPDATA"])
        elif os.environ.get("XDG_DATA_HOME"):
            root = Path(os.environ["XDG_DATA_HOME"])
        else:
            root = Path.home() / ".local" / "share"
        return cls.from_data_dir(root / "xjtu-jwc-notices")

