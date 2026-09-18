from pathlib import Path

from xjtu_jwc_notices.paths import AppPaths


def test_default_paths_use_localappdata_on_windows(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")

    paths = AppPaths.default(platform="win32")

    assert paths.data_dir == Path(r"C:\Users\tester\AppData\Local\xjtu-jwc-notices")
    assert paths.database == paths.data_dir / "notices.db"
    assert paths.cookies == paths.data_dir / "cookies.json"


def test_default_paths_fall_back_to_user_data_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    paths = AppPaths.default(platform="linux")

    assert paths.data_dir == tmp_path / "share" / "xjtu-jwc-notices"


def test_default_paths_honor_explicit_environment_override(monkeypatch, tmp_path):
    custom = tmp_path / "shared-archive"
    monkeypatch.setenv("XJTU_JWC_NOTICES_DATA_DIR", str(custom))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    paths = AppPaths.default(platform="win32")

    assert paths.data_dir == custom
