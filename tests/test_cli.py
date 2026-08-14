"""CLI surface: help, listings, and the flags the README documents.

None of these commands reaches the network.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from hpmaint.main import configure, main, op, run
from hpmaint.maintenance import INDIVIDUAL_OPS, SEQUENCES


@pytest.fixture
def runner():
    return CliRunner()


def _opts(command):
    """Map every declared option flag to its click parameter."""
    found = {}
    for param in command.params:
        for opt in getattr(param, "opts", []) + getattr(param, "secondary_opts", []):
            found[opt] = param
    return found


# ---------------------------------------------------------------- smoke


def test_help_works(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "HP Envy Photo 7855" in result.output


def test_run_list_works(runner):
    result = runner.invoke(main, ["run", "--list"])
    assert result.exit_code == 0
    for key in ("refresh", "standard", "deep", "nuclear"):
        assert key in result.output


def test_op_list_works(runner):
    result = runner.invoke(main, ["op", "--list"])
    assert result.exit_code == 0
    for o in INDIVIDUAL_OPS:
        assert o["key"] in result.output


def test_unknown_sequence_exits_nonzero(runner):
    result = runner.invoke(main, ["run", "bogus"])
    assert result.exit_code == 1
    assert "Unknown sequence" in result.output


def test_subcommand_help_works(runner):
    for name in ("run", "op", "status", "configure"):
        result = runner.invoke(main, [name, "--help"])
        assert result.exit_code == 0, name


# ---------------------------------------------------------------- README flags


def test_readme_group_flags_exist():
    """./hpmaint.py --ip / --password / --config / --debug / --log-file"""
    opts = _opts(main)
    for flag in ("--ip", "--password", "--config", "--debug", "--log-file"):
        assert flag in opts, flag


def test_readme_env_vars_are_wired():
    opts = _opts(main)
    assert opts["--ip"].envvar == "HPMAINT_PRINTER_IP"
    assert opts["--password"].envvar == "HPMAINT_PRINTER_PASSWORD"
    assert opts["--debug"].envvar == "HPMAINT_DEBUG"
    assert opts["--log-file"].envvar == "HPMAINT_LOG_FILE"


def test_readme_run_flags_exist():
    """./hpmaint.py run <name> --repeat N / --list"""
    opts = _opts(run)
    assert "--repeat" in opts
    assert "-r" in opts
    assert "--list" in opts
    assert opts["--repeat"].default == 1


def test_readme_op_flags_exist():
    """./hpmaint.py op <name> -r N / --list"""
    opts = _opts(op)
    assert "--repeat" in opts
    assert "-r" in opts
    assert "--list" in opts


def test_readme_configure_flags_exist():
    opts = _opts(configure)
    assert "--ip" in opts
    assert "--password" in opts
    assert opts["--password"].hide_input is True


def test_readme_sequence_names_exist():
    assert set(SEQUENCES) == {"refresh", "standard", "deep", "nuclear"}


def test_readme_operation_names_exist():
    assert {o["key"] for o in INDIVIDUAL_OPS} == {
        "clean1", "clean2", "align", "quality", "test", "ink",
    }
