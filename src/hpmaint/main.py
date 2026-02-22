"""CLI entry point — interactive menu and unattended mode."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import click
from rich import box
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .config import Config
from .discover import PrinterInfo, discover_printers
from .ews import EWSClient, InkLevel, MaintenanceResult
from .log import get_logger, log_path, setup as log_setup
from .maintenance import (
    INDIVIDUAL_OPS,
    SEQUENCES,
    Sequence,
    SequenceResult,
    Step,
    StepResult,
    run_individual,
    run_sequence,
)
from .theme import COW_ART, console, print_info, print_ok, print_step, print_warn

log = get_logger(__name__)


# ────────────────────────────────────────────────────── helpers

def _ink_bar(pct: int | None, width: int = 12) -> str:
    """Return a simple ASCII bar for ink level."""
    if pct is None:
        return "[moo.dim]" + "?" * width + "[/]"
    filled = round(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    colour = "moo.warn" if (pct is not None and pct < 20) else "moo.ok"
    return f"[{colour}]{bar}[/]"


def _show_ink_table(levels: list[InkLevel]) -> None:
    if not levels:
        print_info("Ink levels not available from EWS.")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="moo.subtitle")
    table.add_column("Colour", style="moo.menu.text")
    table.add_column("Level", justify="right", style="moo.menu.text")
    table.add_column("Bar", no_wrap=True)
    for lvl in levels:
        pct_str = f"{lvl.level_pct}%" if lvl.level_pct is not None else "unknown"
        table.add_row(lvl.label or lvl.color, pct_str, _ink_bar(lvl.level_pct))
    console.print(table)


def _show_result(result: MaintenanceResult) -> None:
    if result.success:
        print_ok(f"[moo.ok]{result.message}[/]")
    else:
        print_warn(f"[moo.warn]{result.message}[/]")
        if result.manual_instructions:
            console.print()
            console.print(Panel(
                result.manual_instructions,
                title="[moo.accent]Manual steps[/]",
                border_style="grey50",
                padding=(1, 2),
            ))


def _discover_or_exit(cfg: Config) -> tuple[PrinterInfo, EWSClient]:
    """Return a connected (PrinterInfo, EWSClient), or exit with a message."""
    if cfg.printer_ip:
        log.info("Using configured printer IP: %s", cfg.printer_ip)
        info = PrinterInfo(ip=cfg.printer_ip, port=cfg.printer_port, via="config")
    else:
        log.info("No IP configured — starting auto-discovery")
        with Progress(
            SpinnerColumn(style="pink1"),
            TextColumn("[moo.subtitle]Searching for printers…[/]"),
            transient=True,
            console=console,
        ) as prog:
            prog.add_task("scan", total=None)
            printers = discover_printers()

        if not printers:
            log.warning("No printers found after discovery")
            console.print(Panel(
                "[moo.warn]No HP printers found on the network.[/]\n\n"
                "Tip: run [moo.accent]hpmaint configure[/] to set a fixed IP,\n"
                "or check the printer is powered on and connected.",
                border_style="red",
                title="[moo.warn]Not found[/]",
            ))
            sys.exit(1)

        if len(printers) == 1:
            info = printers[0]
        else:
            console.print()
            console.print("[moo.subtitle]Multiple printers found:[/]")
            for i, p in enumerate(printers, 1):
                console.print(f"  [moo.menu.num]{i}[/] [moo.menu.text]{p.ip}[/] [moo.dim]{p.name or ''}[/]")
            choice = click.prompt("Select printer", type=click.IntRange(1, len(printers)), default=1)
            info = printers[choice - 1]
        log.info("Selected printer: %s (via %s)", info.ip, info.via)

    client = EWSClient(
        ip=info.ip,
        port=info.ews_port,
        username=cfg.username,
        password=cfg.password,
        timeout=cfg.timeout,
    )

    with Progress(
        SpinnerColumn(style="pink1"),
        TextColumn(f"[moo.subtitle]Connecting to {info.ip}…[/]"),
        transient=True,
        console=console,
    ) as prog:
        prog.add_task("connect", total=None)
        reachable = client.probe()

    if not reachable:
        log.error("EWS at %s did not respond — giving up", info.ip)
        console.print(Panel(
            f"[moo.warn]EWS at {info.ip} is not responding.[/]\n\n"
            "The printer may be asleep, offline, or using a non-standard port.\n"
            "Try waking it first (press the power button or send a print job).\n\n"
            f"If authentication is required, run [moo.accent]hpmaint configure[/] to set credentials.",
            border_style="red",
            title="[moo.warn]Connection failed[/]",
        ))
        sys.exit(1)
    log.info("EWS connection OK: %s", info.ip)

    print_ok(f"Connected: [bold white]{info.ip}[/]")
    return info, client


def _run_sequence_interactive(
    client: EWSClient, seq: Sequence, repeat: int = 1
) -> None:
    for run_num in range(1, repeat + 1):
        if repeat > 1:
            console.print(Rule(f"[moo.accent]Run {run_num}/{repeat}[/]", style="grey50"))

        def on_start(step: Step, n: int, total: int) -> None:
            console.print()
            console.print(f"  [moo.menu.num]{n}/{total}[/] [moo.menu.text]{step.label}[/]  [moo.dim]{step.description}[/]")

        def on_done(sr: StepResult) -> None:
            _show_result(sr.result)

        def on_wait(secs: int) -> None:
            with Progress(
                SpinnerColumn(style="grey50"),
                TextColumn(f"[moo.dim]Waiting {secs}s for ink to settle…[/]"),
                TimeElapsedColumn(),
                transient=True,
                console=console,
            ) as prog:
                task = prog.add_task("wait", total=secs)
                for _ in range(secs):
                    time.sleep(1)
                    prog.advance(task, 1)

        run_sequence(
            client,
            seq,
            on_step_start=on_start,
            on_step_done=on_done,
            on_wait=on_wait,
        )


# ────────────────────────────────────────────────────── CLI


@click.group(invoke_without_command=True)
@click.option("--ip", envvar="HPMAINT_PRINTER_IP", default="", help="Printer IP (skips discovery)")
@click.option("--password", envvar="HPMAINT_PRINTER_PASSWORD", default="", help="EWS password")
@click.option("--config", "config_path", default=None, help="Path to config file")
@click.option("--debug", is_flag=True, envvar="HPMAINT_DEBUG",
              help="Print debug logs to stderr in addition to the log file")
@click.option("--log-file", "log_file", default=None, envvar="HPMAINT_LOG_FILE",
              help="Override log file path (default: ~/.local/share/hpmaint/hpmaint.log)")
@click.pass_context
def main(
    ctx: click.Context,
    ip: str,
    password: str,
    config_path: str | None,
    debug: bool,
    log_file: str | None,
) -> None:
    """HP Envy Photo 7855 Printer Maintenance"""
    ctx.ensure_object(dict)

    # Initialise logging first so every subsequent call is captured
    lp = log_setup(debug=debug, log_file=Path(log_file) if log_file else None)
    log.info("=" * 60)
    log.info("hpmaint started  args=%s", sys.argv[1:])

    cfg = Config(path=None if config_path is None else Path(config_path))
    if ip:
        cfg.printer_ip = ip
    if password:
        cfg.password = password
    ctx.obj["cfg"] = cfg

    if ctx.invoked_subcommand is None:
        _interactive_menu(cfg, show_log_path=True)
    else:
        # Show log path hint for subcommands too
        print_info(f"Log: {lp}")


# ────────────────────────────────────────────────── interactive menu


def _interactive_menu(cfg: Config, show_log_path: bool = False) -> None:
    console.print(Text(COW_ART, style="white"))
    lp = log_path()
    subtitle = "[moo.subtitle]HP Envy Photo 7855  —  Printer Maintenance[/]"
    if lp:
        subtitle += f"\n[moo.dim]Log: {lp}[/]"
    console.print(Panel(
        subtitle,
        border_style="grey50",
        expand=False,
        padding=(0, 2),
    ))
    console.print()

    _, client = _discover_or_exit(cfg)

    while True:
        console.print()
        console.print(Rule("[moo.accent]Main Menu[/]", style="grey50"))
        console.print()

        # Sequences
        seq_keys = list(SEQUENCES.keys())
        console.print("  [moo.subtitle]── Sequences ──[/]")
        for i, key in enumerate(seq_keys, 1):
            seq = SEQUENCES[key]
            console.print(
                f"  [moo.menu.num]{i}[/]  [moo.menu.text]{seq.name}[/]  "
                f"[moo.dim]{seq.description} · {seq.idle_days}[/]"
            )

        # Individual ops
        console.print()
        console.print("  [moo.subtitle]── Individual ──[/]")
        for j, op in enumerate(INDIVIDUAL_OPS, len(seq_keys) + 1):
            console.print(
                f"  [moo.menu.num]{j}[/]  [moo.menu.text]{op['name']}[/]  "
                f"[moo.dim]{op['description']}[/]"
            )

        # Extras
        console.print()
        console.print("  [moo.subtitle]── Other ──[/]")
        n_configure = len(seq_keys) + len(INDIVIDUAL_OPS) + 1
        console.print(f"  [moo.menu.num]{n_configure}[/]  [moo.menu.text]Configure[/]  [moo.dim]Set printer IP / credentials[/]")
        console.print("  [moo.menu.num]q[/]  [moo.menu.text]Quit[/]")
        console.print()

        console.print("[moo.accent]Select[/] ", end="")
        choice = click.prompt("", default="q", prompt_suffix="› ", show_default=False)
        choice = choice.strip().lower()

        if choice == "q":
            console.print("\n[moo.dim]Done.[/]\n")
            break

        try:
            idx = int(choice)
        except ValueError:
            print_warn("Invalid choice.")
            continue

        if 1 <= idx <= len(seq_keys):
            seq = SEQUENCES[seq_keys[idx - 1]]
            console.print()
            console.print(Panel(
                f"[moo.menu.text]{seq.description}[/]\n"
                f"[moo.dim]Use when: {seq.idle_days}[/]\n\n"
                + "\n".join(
                    f"  [moo.dim]{i+1}.[/] {s.label}"
                    + (f"  [moo.dim](+{s.wait_after}s wait)[/]" if s.wait_after else "")
                    for i, s in enumerate(seq.steps)
                ),
                title=f"[moo.accent]{seq.name}[/]",
                border_style="grey50",
                padding=(1, 2),
            ))
            repeat = click.prompt("  Repeat how many times", default=1, type=click.IntRange(1, 10))
            console.print(f"  Run [bold white]{seq.name}[/] ×{repeat}? ", end="")
            if click.confirm("", default=True, prompt_suffix=""):
                _run_sequence_interactive(client, seq, repeat=repeat)

        elif len(seq_keys) < idx <= len(seq_keys) + len(INDIVIDUAL_OPS):
            op = INDIVIDUAL_OPS[idx - len(seq_keys) - 1]
            console.print()
            repeat = click.prompt(f"  Repeat {op['name']} how many times", default=1, type=click.IntRange(1, 10))
            for r in range(repeat):
                if repeat > 1:
                    console.print(f"  [moo.dim]Run {r+1}/{repeat}[/]")
                result = run_individual(client, op["key"])
                _show_result(result)
                if op["key"] == "ink":
                    levels = client.get_ink_levels()
                    _show_ink_table(levels)

        elif idx == n_configure:
            _configure_menu(cfg)

        else:
            print_warn("Out of range.")


def _configure_menu(cfg: Config) -> None:
    console.print()
    console.print(Rule("[moo.accent]Configure[/]", style="grey50"))
    current_ip = cfg.printer_ip or "(auto-discover)"
    new_ip = click.prompt(f"  Printer IP [{current_ip}]", default=cfg.printer_ip or "", show_default=False)
    if new_ip:
        cfg.printer_ip = new_ip
    new_pw = click.prompt("  EWS password (blank = none)", default="", hide_input=True, show_default=False)
    if new_pw:
        cfg.password = new_pw
    cfg.save()
    print_ok("Saved.")


# ────────────────────────────────────────────────── subcommands


@main.command()
@click.argument("sequence_name", default="")
@click.option("--repeat", "-r", default=1, type=click.IntRange(1, 20), help="Run sequence N times")
@click.option("--list", "list_sequences", is_flag=True, help="List available sequences")
@click.pass_context
def run(ctx: click.Context, sequence_name: str, repeat: int, list_sequences: bool) -> None:
    """Run a maintenance sequence unattended.

    \b
    SEQUENCE_NAME: refresh | standard | deep | nuclear
    """
    if list_sequences or not sequence_name:
        table = Table(box=box.SIMPLE, show_header=True, header_style="moo.subtitle")
        table.add_column("Key", style="moo.menu.num")
        table.add_column("Name", style="moo.menu.text")
        table.add_column("When", style="moo.dim")
        table.add_column("Description", style="moo.dim")
        for seq in SEQUENCES.values():
            table.add_row(seq.key, seq.name, seq.idle_days, seq.description)
        console.print(table)
        return

    seq = SEQUENCES.get(sequence_name)
    if not seq:
        console.print(f"[moo.warn]Unknown sequence: {sequence_name!r}[/]")
        console.print(f"Available: {', '.join(SEQUENCES)}")
        sys.exit(1)

    cfg: Config = ctx.obj["cfg"]
    _, client = _discover_or_exit(cfg)
    _run_sequence_interactive(client, seq, repeat=repeat)


@main.command()
@click.argument("operation", required=False, default="")
@click.option("--repeat", "-r", default=1, type=click.IntRange(1, 20))
@click.option("--list", "list_ops", is_flag=True, help="List available operations")
@click.pass_context
def op(ctx: click.Context, operation: str, repeat: int, list_ops: bool) -> None:
    """Run a single maintenance operation.

    \b
    OPERATION: clean1 | clean2 | align | quality | test | ink
    """
    if list_ops or not operation:
        for o in INDIVIDUAL_OPS:
            console.print(f"  [moo.menu.num]{o['key']:<10}[/] [moo.menu.text]{o['name']}[/]  [moo.dim]{o['description']}[/]")
        return

    cfg: Config = ctx.obj["cfg"]
    _, client = _discover_or_exit(cfg)
    for i in range(repeat):
        if repeat > 1:
            console.print(f"[moo.dim]Run {i+1}/{repeat}[/]")
        result = run_individual(client, operation)
        _show_result(result)
        if operation == "ink":
            _show_ink_table(client.get_ink_levels())


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show printer status and ink levels."""
    cfg: Config = ctx.obj["cfg"]
    info, client = _discover_or_exit(cfg)
    s = client.get_status()
    console.print()
    console.print(Panel(
        f"[moo.menu.text]IP:[/]    [bold white]{info.ip}[/]\n"
        f"[moo.menu.text]Model:[/] [bold white]{s.model or 'HP Envy Photo 7855'}[/]\n"
        f"[moo.menu.text]EWS:[/]   [moo.dim]{s.ews_url}[/]",
        title="[moo.accent]Printer[/]",
        border_style="grey50",
        padding=(1, 2),
    ))
    console.print()
    _show_ink_table(s.ink)


@main.command()
@click.option("--ip", prompt="Printer IP (blank = auto-discover)", default="")
@click.option("--password", prompt="EWS password (blank = none)", default="", hide_input=True)
@click.pass_context
def configure(ctx: click.Context, ip: str, password: str) -> None:
    """Save printer IP and credentials to config file."""
    cfg: Config = ctx.obj["cfg"]
    if ip:
        cfg.printer_ip = ip
    if password:
        cfg.password = password
    cfg.save()
    print_ok(f"Saved to {cfg._path}")
