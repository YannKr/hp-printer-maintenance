"""Maintenance sequences and individual operations.

Sequences are research-backed routines for HP thermal inkjet heads:
- HP recommends light cleaning after ~7 days idle.
- Two consecutive deep cleans handle moderate clogs.
- Severe clogs benefit from a soak period between heavy cycles.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ews import EWSClient, MaintenanceResult


# Minimum gap between consecutive steps. Even when a step finishes "instantly"
# (HTTP 201), the printer's nginx briefly returns 503 if another POST arrives in
# the same second — it's still spooling the previous job.
MIN_STEP_GAP = 10


# ------------------------------------------------------------------ sequences


@dataclass
class Step:
    label: str
    description: str
    wait_after: int = 0   # seconds to pause after this step


@dataclass
class Sequence:
    key: str
    name: str
    description: str
    idle_days: str        # human-readable when to use
    steps: list[Step] = field(default_factory=list)


SEQUENCES: dict[str, Sequence] = {
    "refresh": Sequence(
        key="refresh",
        name="Quick Refresh",
        description="1× light clean + test print",
        idle_days="1–7 days idle",
        steps=[
            Step("Light clean ×1", "Unclogs minor dried ink in nozzles", wait_after=120),
            Step("Test print", "Verify output quality"),
        ],
    ),
    "standard": Sequence(
        key="standard",
        name="Standard Maintenance",
        description="2× light clean → alignment → test print",
        idle_days="1–4 weeks idle",
        steps=[
            Step("Light clean ×1", "First light cleaning pass", wait_after=120),
            Step("Light clean ×2", "Second light cleaning pass", wait_after=90),
            Step("Align printhead", "Corrects banding and colour registration"),
            Step("Test print", "Verify output quality"),
        ],
    ),
    "deep": Sequence(
        key="deep",
        name="Deep Clean",
        description="1× deep + 1× light → alignment → test print",
        idle_days="1–3 months idle",
        steps=[
            Step("Deep clean ×1", "Heavy purge cycle for dried ink", wait_after=300),
            Step("Light clean ×1", "Flush residue after deep clean", wait_after=120),
            Step("Align printhead", "Re-align after heavy cleaning"),
            Step("Print quality report", "Inspect nozzle pattern"),
            Step("Test print", "Final output verification"),
        ],
    ),
    "nuclear": Sequence(
        key="nuclear",
        name="Nuclear Option",
        description="3× deep → 10 min soak → 2× deep → alignment",
        idle_days="3+ months idle / severe clogs",
        steps=[
            Step("Deep clean ×1", "First heavy purge", wait_after=60),
            Step("Deep clean ×2", "Second heavy purge", wait_after=60),
            Step("Deep clean ×3", "Third heavy purge — soak period follows", wait_after=600),
            Step("Deep clean ×4", "Post-soak purge", wait_after=60),
            Step("Deep clean ×5", "Final purge cycle", wait_after=300),
            Step("Light clean ×1", "Flush cycle", wait_after=120),
            Step("Align printhead", "Re-align after intensive cleaning"),
            Step("Print quality report", "Inspect nozzle pattern"),
            Step("Test print", "Final output verification"),
        ],
    ),
}


# ------------------------------------------------------------------ runner


@dataclass
class StepResult:
    step: Step
    result: "MaintenanceResult"
    elapsed: float


@dataclass
class SequenceResult:
    sequence: Sequence
    steps: list[StepResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(r.result.success for r in self.steps)

    @property
    def n_ok(self) -> int:
        return sum(1 for r in self.steps if r.result.success)


def run_sequence(
    client: "EWSClient",
    sequence: Sequence,
    on_step_start: "Callable[[Step, int, int], None] | None" = None,
    on_step_done: "Callable[[StepResult], None] | None" = None,
    on_wait: "Callable[[int], None] | None" = None,
) -> SequenceResult:
    from typing import Callable

    result = SequenceResult(sequence=sequence)
    total = len(sequence.steps)

    for i, step in enumerate(sequence.steps):
        if on_step_start:
            on_step_start(step, i + 1, total)

        t0 = time.monotonic()
        op_result = _dispatch_step(client, step)
        elapsed = time.monotonic() - t0

        sr = StepResult(step=step, result=op_result, elapsed=elapsed)
        result.steps.append(sr)

        if on_step_done:
            on_step_done(sr)

        if i < total - 1:
            gap = max(step.wait_after, MIN_STEP_GAP)
            if on_wait:
                on_wait(gap)
            else:
                time.sleep(gap)

    return result


def _dispatch_step(client: "EWSClient", step: Step) -> "MaintenanceResult":
    label = step.label.lower()
    if "deep clean" in label:
        return client.clean_printhead(level=2)
    if "light clean" in label:
        return client.clean_printhead(level=1)
    if "align" in label:
        return client.align_printhead()
    if "quality report" in label or "quality" in label:
        return client.print_quality_report()
    if "test print" in label:
        return client.print_test_page()
    # Unknown — attempt clean as safe default
    from .ews import MaintenanceResult
    return MaintenanceResult(
        success=False,
        message=f"Unknown step type: {step.label!r}",
    )


# ------------------------------------------------------------------ individual ops


INDIVIDUAL_OPS: list[dict[str, str]] = [
    {"key": "clean1", "name": "Light clean", "description": "Quick nozzle flush (~2 min)"},
    {"key": "clean2", "name": "Deep clean", "description": "Heavy purge, uses more ink (~5 min)"},
    {"key": "align",  "name": "Align printhead", "description": "Prints + scans alignment page"},
    {"key": "quality","name": "Quality report", "description": "Nozzle test pattern page"},
    {"key": "test",   "name": "Test page", "description": "Colour demo / status page"},
    {"key": "ink",    "name": "Ink levels", "description": "Read ink cartridge levels from EWS"},
]


def run_individual(client: "EWSClient", key: str) -> "MaintenanceResult":
    if key == "clean1":
        return client.clean_printhead(level=1)
    if key == "clean2":
        return client.clean_printhead(level=2)
    if key == "align":
        return client.align_printhead()
    if key == "quality":
        return client.print_quality_report()
    if key == "test":
        return client.print_test_page()
    if key == "ink":
        from .ews import MaintenanceResult
        levels = client.get_ink_levels()
        if levels:
            summary = ", ".join(
                f"{l.label} {l.level_pct}%" if l.level_pct is not None else l.label
                for l in levels
            )
            return MaintenanceResult(success=True, message=f"Ink: {summary}")
        return MaintenanceResult(
            success=False,
            message="Could not read ink levels from EWS",
            manual_instructions=(
                "Check ink levels:\n"
                "  Printer touchscreen → Estimated Ink Levels\n"
                "  or open the printer's EWS in a browser."
            ),
        )
    from .ews import MaintenanceResult
    return MaintenanceResult(success=False, message=f"Unknown operation: {key!r}")
