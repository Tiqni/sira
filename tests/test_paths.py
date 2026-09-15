"""Where Sira keeps its runtime state on disk."""

from pathlib import Path

import platformdirs

from sira import paths


def test_data_dir_honours_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "custom"))
    assert paths.data_dir() == tmp_path / "custom"


def test_data_dir_defaults_to_the_platform_user_data_dir(monkeypatch):
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    assert paths.data_dir() == Path(platformdirs.user_data_dir("sira"))


def test_memory_db_path_lives_inside_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path))
    assert paths.memory_db_path() == tmp_path / "resume_memory.sqlite3"


def test_dbos_db_url_points_inside_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path))
    assert paths.dbos_db_url() == f"sqlite:///{tmp_path / 'dbos.sqlite3'}"


def test_migrate_legacy_memory_db_moves_the_old_file(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "memory" / "resume_memory.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"old")

    moved = paths.migrate_legacy_memory_db()

    assert moved == tmp_path / "data" / "resume_memory.sqlite3"
    assert moved.read_bytes() == b"old"
    assert not legacy.exists()


def test_migrate_legacy_memory_db_moves_wal_sidecars_too(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    legacy_dir = tmp_path / "memory"
    legacy_dir.mkdir()
    for suffix in ("", "-wal", "-shm"):
        (legacy_dir / f"resume_memory.sqlite3{suffix}").write_bytes(b"x")

    paths.migrate_legacy_memory_db()

    for suffix in ("", "-wal", "-shm"):
        assert (tmp_path / "data" / f"resume_memory.sqlite3{suffix}").exists()
        assert not (legacy_dir / f"resume_memory.sqlite3{suffix}").exists()


def test_migrate_legacy_memory_db_is_a_no_op_without_a_legacy_file(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)

    assert paths.migrate_legacy_memory_db() is None
    assert not (tmp_path / "data").exists()


def test_migrate_legacy_memory_db_never_overwrites_the_new_db(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "memory" / "resume_memory.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"old")
    current = tmp_path / "data" / "resume_memory.sqlite3"
    current.parent.mkdir()
    current.write_bytes(b"new")

    assert paths.migrate_legacy_memory_db() is None
    assert current.read_bytes() == b"new"
    assert legacy.read_bytes() == b"old"
