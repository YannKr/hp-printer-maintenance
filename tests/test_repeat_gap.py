"""Fault 5 — MIN_STEP_GAP must apply across repeats, not only within one."""

from __future__ import annotations

import pytest

from hpmaint import main as main_mod
from hpmaint.ews import MaintenanceResult
from hpmaint.maintenance import MIN_STEP_GAP, SEQUENCES


class _StubClient:
    """Records calls; never touches the network."""

    def __init__(self):
        self.calls: list[str] = []

    def _ok(self, name):
        self.calls.append(name)
        return MaintenanceResult(success=True, message="ok")

    def clean_printhead(self, level=1):
        return self._ok(f"clean{level}")

    def align_printhead(self):
        return self._ok("align")

    def print_quality_report(self):
        return self._ok("quality")

    def print_test_page(self):
        return self._ok("test")

    def get_ink_levels(self):
        self.calls.append("ink")
        return []


@pytest.fixture
def gaps(monkeypatch):
    """Capture every between-run pause instead of sleeping."""
    recorded: list[int] = []
    monkeypatch.setattr(main_mod, "_wait_between_runs", recorded.append)
    # Steps inside a sequence still wait; make that instant too.
    monkeypatch.setattr("time.sleep", lambda _s: None)
    return recorded


def test_sequence_repeats_wait_between_runs(gaps, monkeypatch):
    monkeypatch.setattr(main_mod, "run_sequence", lambda *a, **k: None)
    main_mod._run_sequence_interactive(_StubClient(), SEQUENCES["refresh"], repeat=3)
    assert len(gaps) == 2                      # between run 1-2 and 2-3, not after
    assert all(g >= MIN_STEP_GAP for g in gaps)


def test_single_sequence_run_waits_for_nothing(gaps, monkeypatch):
    monkeypatch.setattr(main_mod, "run_sequence", lambda *a, **k: None)
    main_mod._run_sequence_interactive(_StubClient(), SEQUENCES["refresh"], repeat=1)
    assert gaps == []


def test_op_repeats_wait_between_runs(gaps, monkeypatch):
    """The README's own `op clean2 -r 3`."""
    client = _StubClient()
    monkeypatch.setattr(main_mod, "_discover_or_exit", lambda cfg: (None, client))

    from click.testing import CliRunner

    result = CliRunner().invoke(main_mod.main, ["op", "clean2", "-r", "3"])
    assert result.exit_code == 0, result.output
    assert client.calls == ["clean2", "clean2", "clean2"]
    assert gaps == [MIN_STEP_GAP, MIN_STEP_GAP]


def test_op_single_run_waits_for_nothing(gaps, monkeypatch):
    client = _StubClient()
    monkeypatch.setattr(main_mod, "_discover_or_exit", lambda cfg: (None, client))

    from click.testing import CliRunner

    result = CliRunner().invoke(main_mod.main, ["op", "clean2"])
    assert result.exit_code == 0, result.output
    assert gaps == []


def test_ink_reads_do_not_wait(gaps, monkeypatch):
    """`ink` is a GET, so it cannot trip the printer's 503."""
    client = _StubClient()
    monkeypatch.setattr(main_mod, "_discover_or_exit", lambda cfg: (None, client))

    from click.testing import CliRunner

    result = CliRunner().invoke(main_mod.main, ["op", "ink", "-r", "3"])
    assert result.exit_code == 0, result.output
    assert gaps == []
