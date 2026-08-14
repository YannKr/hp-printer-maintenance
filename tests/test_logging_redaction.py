"""Fault 1 — the password must never reach the log file."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from hpmaint.main import _redact_args, main


def test_redacts_separate_value():
    assert _redact_args(["--password", "s3cr3t", "run", "standard"]) == [
        "--password", "***", "run", "standard",
    ]


def test_redacts_inline_value():
    assert _redact_args(["--password=s3cr3t", "status"]) == [
        "--password=***", "status",
    ]


def test_redacts_readme_example():
    # The exact line the README tells users to run.
    argv = ["--password", "secret", "run", "standard"]
    assert "secret" not in " ".join(_redact_args(argv))


def test_leaves_other_arguments_alone():
    argv = ["op", "clean2", "-r", "3", "--ip", "192.0.2.1"]
    assert _redact_args(argv) == argv


def test_trailing_password_flag_without_value():
    assert _redact_args(["--password"]) == ["--password"]


# ---------------------------------------------------------------- end to end
#
# The tests above only exercise the helper. These run the real CLI and read
# the real log file, so removing the call site in main() fails them.


def _log_text() -> str:
    return Path(os.environ["HPMAINT_LOG_FILE"]).read_text()


def _run(monkeypatch, argv):
    """Invoke the CLI with a real sys.argv, since that is what main() logs."""
    monkeypatch.setattr(sys, "argv", ["hpmaint", *argv])
    return CliRunner().invoke(main, argv)


def test_password_never_reaches_the_log_file(monkeypatch):
    """`hpmaint --password secret run --list`, exactly as the README shows."""
    result = _run(monkeypatch, ["--password", "sup3rs3cret", "run", "--list"])
    assert result.exit_code == 0, result.output

    written = _log_text()
    assert "hpmaint started" in written        # the line really was logged
    assert "sup3rs3cret" not in written
    assert "--password" in written
    assert "***" in written


def test_inline_password_never_reaches_the_log_file(monkeypatch):
    result = _run(monkeypatch, ["--password=sup3rs3cret", "op", "--list"])
    assert result.exit_code == 0, result.output

    written = _log_text()
    assert "hpmaint started" in written
    assert "sup3rs3cret" not in written
    assert "--password=***" in written


def test_password_from_the_environment_never_reaches_the_log_file(monkeypatch):
    monkeypatch.setenv("HPMAINT_PRINTER_PASSWORD", "envs3cret")
    result = _run(monkeypatch, ["run", "--list"])
    assert result.exit_code == 0, result.output
    assert "envs3cret" not in _log_text()


def test_other_arguments_are_still_logged(monkeypatch):
    """Redaction must not blank the whole command line."""
    result = _run(monkeypatch, ["--password", "s3cret", "op", "--list"])
    assert result.exit_code == 0, result.output

    written = _log_text()
    assert "'op'" in written
    assert "'--list'" in written


def test_real_process_writes_no_password_to_its_log(tmp_path):
    """Belt and braces: a separate interpreter, its own argv, its own log file.

    `run --list` returns before any discovery, so no network is touched.
    """
    log_file = tmp_path / "subprocess" / "hpmaint.log"
    env = dict(
        os.environ,
        HPMAINT_LOG_FILE=str(log_file),
        HPMAINT_CONFIG=str(tmp_path / "config.toml"),
    )
    env.pop("HPMAINT_PRINTER_PASSWORD", None)

    proc = subprocess.run(
        [sys.executable, "-c", "from hpmaint.main import main; main()",
         "--password", "pr0cs3cret", "run", "--list"],
        # cwd matters: the repo root holds a hpmaint.py launcher that would
        # shadow the installed package.
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    written = log_file.read_text()
    assert "hpmaint started" in written
    assert "pr0cs3cret" not in written
    assert "***" in written
    # And it must not leak on the terminal either.
    assert "pr0cs3cret" not in proc.stdout
