"""Fault 1 — the password must never reach the log file."""

from __future__ import annotations

from hpmaint.main import _redact_args


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
