"""`sira setup` installs the browser Playwright drives."""

import subprocess
import sys
from unittest.mock import patch

from typer.testing import CliRunner

from sira.main import app

runner = CliRunner()


def test_setup_installs_chromium_with_the_running_interpreter():
    """The browser must match the playwright version in Sira's own environment.

    `uv tool install sira` / `pipx install sira` do not put the `playwright`
    executable on PATH, so the command runs the module with `sys.executable`.
    """
    with patch("sira.main.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0, result.output
    run.assert_called_once_with(
        [sys.executable, "-m", "playwright", "install", "chromium"], check=False
    )


def test_setup_fails_when_the_browser_install_fails():
    with patch("sira.main.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=3)
        result = runner.invoke(app, ["setup"])

    assert result.exit_code == 1
    assert "Browser install failed" in result.output
