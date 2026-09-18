from xjtu_jwc_notices.paths import AppPaths


def test_default_paths_use_project_data_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("XJTU_JWC_NOTICES_DATA_DIR", raising=False)

    paths = AppPaths.default(project_root=tmp_path)

    assert paths.data_dir == tmp_path / "data"
    assert paths.database == paths.data_dir / "notices.db"
    assert paths.cookies == paths.data_dir / "cookies.json"


def test_default_paths_honor_explicit_environment_override(monkeypatch, tmp_path):
    custom = tmp_path / "shared-archive"
    monkeypatch.setenv("XJTU_JWC_NOTICES_DATA_DIR", str(custom))

    paths = AppPaths.default(project_root=tmp_path / "repository")

    assert paths.data_dir == custom
