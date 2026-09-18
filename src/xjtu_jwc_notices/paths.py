"""Application data paths."""

from __future__ import annotations

import os
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
    def default(cls, *, project_root: Path | None = None) -> AppPaths:
        if os.environ.get("XJTU_JWC_NOTICES_DATA_DIR"):
            return cls.from_data_dir(os.environ["XJTU_JWC_NOTICES_DATA_DIR"])
        root = project_root or Path(__file__).resolve().parents[2]
        return cls.from_data_dir(root / "data")
