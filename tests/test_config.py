"""Faults 9 and 10 — config file writing, reading and permissions."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from hpmaint.config import Config, _toml_value

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

AWKWARD = 'my"pass\\word'


def _cfg(tmp_path, monkeypatch):
    monkeypatch.delenv("HPMAINT_PRINTER_PASSWORD", raising=False)
    monkeypatch.delenv("HPMAINT_PRINTER_IP", raising=False)
    return Config(path=tmp_path / "hpmaint" / "config.toml")


# ---------------------------------------------------------------- fault 9, writing


@pytest.mark.parametrize("value", [
    'my"pass',
    "back\\slash",
    'both"and\\',
    "tab\there",
    "new\nline",
    "plain",
])
def test_written_values_can_be_read_back(value):
    doc = f"[printer]\npassword = {_toml_value(value)}\n"
    assert tomllib.loads(doc)["printer"]["password"] == value


def test_quote_is_escaped():
    assert _toml_value('my"pass') == '"my\\"pass"'


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.password = AWKWARD
    cfg.printer_ip = "192.0.2.7"
    cfg.save()

    again = Config(path=cfg._path)
    assert again.password == AWKWARD
    assert again.printer_ip == "192.0.2.7"


def test_hand_rolled_writer_round_trips(tmp_path, monkeypatch):
    """The fallback used when tomli_w is absent must also be valid TOML."""
    import builtins

    real_import = builtins.__import__

    def no_tomli_w(name, *args, **kwargs):
        if name == "tomli_w":
            raise ImportError("no tomli_w")
        return real_import(name, *args, **kwargs)

    cfg = _cfg(tmp_path, monkeypatch)
    cfg.password = AWKWARD
    monkeypatch.setattr(builtins, "__import__", no_tomli_w)
    cfg.save()
    monkeypatch.setattr(builtins, "__import__", real_import)

    assert Config(path=cfg._path).password == AWKWARD


# ---------------------------------------------------------------- fault 9, reading


def test_broken_config_gives_a_clear_message(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[printer]\npassword = "my"pass"\n')
    with pytest.raises(SystemExit) as exc:
        Config(path=path)
    message = str(exc.value)
    assert "not valid TOML" in message
    assert str(path) in message
    assert "rm " in message


# ---------------------------------------------------------------- fault 10


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_config_file_is_written_0600(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.password = "secret"
    cfg.save()
    assert stat.S_IMODE(os.stat(cfg._path).st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_new_config_directory_is_0700(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.save()
    assert stat.S_IMODE(os.stat(cfg._path.parent).st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_existing_world_readable_directory_is_tightened(tmp_path, monkeypatch):
    """mkdir(mode=...) does nothing to a directory that already exists."""
    cfg = _cfg(tmp_path, monkeypatch)
    cfg._path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg._path.parent, 0o755)
    assert stat.S_IMODE(os.stat(cfg._path.parent).st_mode) == 0o755

    cfg.password = "secret"
    cfg.save()
    assert stat.S_IMODE(os.stat(cfg._path.parent).st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_an_unrelated_existing_directory_is_left_alone(tmp_path, monkeypatch):
    """--config can name any path. Never chmod a directory that is not ours."""
    home = tmp_path / "pretend-home"
    home.mkdir(mode=0o755)
    os.chmod(home, 0o755)

    monkeypatch.delenv("HPMAINT_PRINTER_PASSWORD", raising=False)
    cfg = Config(path=home / "hpmaint.toml")
    cfg.password = "secret"
    cfg.save()

    assert stat.S_IMODE(os.stat(home).st_mode) == 0o755      # untouched
    assert stat.S_IMODE(os.stat(cfg._path).st_mode) == 0o600  # file still safe


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_existing_world_readable_file_is_tightened(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.save()
    os.chmod(cfg._path, 0o644)
    cfg.password = "secret"
    cfg.save()
    assert stat.S_IMODE(os.stat(cfg._path).st_mode) == 0o600
