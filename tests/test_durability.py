"""DBOS runtime configuration for durable runs."""

from importlib import metadata

import pytest
from dbos import DBOS

from sira import durability


def test_build_config_defaults(monkeypatch):
    monkeypatch.delenv(durability.DATABASE_URL_ENV, raising=False)
    cfg = durability.build_config()
    assert cfg["name"] == "sira"
    assert cfg["system_database_url"] == "sqlite:///memory/dbos.sqlite3"
    assert cfg["run_admin_server"] is False
    assert cfg["log_level"] == "CRITICAL"
    assert cfg["application_version"] == durability.application_version()
    assert "enable_otlp" not in cfg


def test_executor_id_is_unique_per_config():
    a = durability.build_config()["executor_id"]
    b = durability.build_config()["executor_id"]
    assert a != b and len(a) == 36  # uuid4 text


def test_application_version_matches_installed_package():
    assert durability.application_version() == metadata.version("sira")


def test_database_url_env_override(monkeypatch):
    monkeypatch.setenv(durability.DATABASE_URL_ENV, "sqlite:////tmp/x.sqlite3")
    assert durability.database_url() == "sqlite:////tmp/x.sqlite3"
    # An explicit argument wins over the environment.
    assert durability.database_url("sqlite:///y.sqlite3") == "sqlite:///y.sqlite3"


def test_tracing_flag_adds_otel_settings():
    cfg = durability.build_config(tracing=True)
    assert cfg["enable_otlp"] is True
    assert cfg["otel_attribute_format"] == "semconv"
    assert "otlp_traces_endpoints" not in cfg


def test_sqlite_parent_directory_is_created(tmp_path):
    url = f"sqlite:///{tmp_path}/nested/dir/dbos.sqlite3"
    durability.build_config(url)
    assert (tmp_path / "nested" / "dir").is_dir()


def test_durable_runtime_is_owned_by_outermost_caller(tmp_path):
    was_active = durability.is_active()
    with durability.durable_runtime(f"sqlite:///{tmp_path}/dbos.sqlite3"):
        assert durability.is_active()
        with durability.durable_runtime():  # nested: no-op
            assert durability.is_active()
        assert durability.is_active()
    assert durability.is_active() == was_active


# Registered at import time: DBOS workflows must exist before the runtime is
# launched (the session fixture launches it once for the whole suite).
@DBOS.workflow(name="sira.test_probe")
async def _probe_workflow(x: int) -> int:
    return x * 2


@pytest.mark.anyio
async def test_workflow_runs_inside_durable_runtime(tmp_path):
    with durability.durable_runtime(f"sqlite:///{tmp_path}/dbos.sqlite3"):
        assert await _probe_workflow(21) == 42


def test_launch_failure_destroys_the_half_built_runtime(monkeypatch, tmp_path):
    """A failed launch must not leave DBOS's global singleton behind."""
    events: list[str] = []

    class FakeDBOS:
        def __init__(self, *, config):
            events.append("construct")

        @classmethod
        def launch(cls):
            events.append("launch")
            raise RuntimeError("launch failed")

        @classmethod
        def destroy(cls, **kwargs):
            events.append("destroy")

    monkeypatch.setattr(durability, "DBOS", FakeDBOS)
    monkeypatch.setattr(durability, "_active", False)
    with pytest.raises(RuntimeError, match="launch failed"):
        with durability.durable_runtime(f"sqlite:///{tmp_path}/d.sqlite3"):
            pass
    assert events == ["construct", "launch", "destroy"]
    assert durability.is_active() is False
