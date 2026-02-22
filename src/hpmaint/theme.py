"""Cow-themed Rich styles and ASCII art."""

from rich.console import Console
from rich.style import Style
from rich.theme import Theme

# Cow colour palette: white/grey body, black spots, red/pink accents
COW_THEME = Theme(
    {
        "moo.title": "bold white",
        "moo.subtitle": "grey70",
        "moo.highlight": "bold bright_white",
        "moo.accent": "bold pink1",
        "moo.warn": "bold red",
        "moo.ok": "bold white",
        "moo.dim": "grey50",
        "moo.menu.num": "bold pink1",
        "moo.menu.text": "white",
        "moo.menu.desc": "grey70",
        "moo.border": "grey50",
        "moo.ink.black": "bold white",   # black ink shown white-on-dark
        "moo.ink.cyan": "bold cyan",
        "moo.ink.magenta": "bold magenta",
        "moo.ink.yellow": "bold yellow",
        "moo.ink.photo": "bold pink1",
    }
)

console = Console(theme=COW_THEME)

# ASCII art — tasteful, not cringe
COW_ART = r"""
  ___________________________________________
 /                                           \
|      HP Envy Photo 7855 — Maintenance        |
 \___________________________________________/
        \   ^__^
         \  (oo)\_______
            (__)\       )\/\
                ||----w |
                ||     ||
"""

COW_SMALL = r"""
   ^__^
   (oo)\_____
   (__)\     )\/\
       ||-w |
       ||   ||"""


def print_banner(subtitle: str = "HP Envy Photo 7855") -> None:
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    cow = Text(COW_ART, style="white")
    console.print(cow)
    console.print(
        Panel(
            f"[moo.subtitle]{subtitle}[/]",
            border_style="grey50",
            expand=False,
            padding=(0, 2),
        )
    )


def print_step(msg: str, indent: int = 0) -> None:
    pad = "  " * indent
    console.print(f"{pad}[moo.dim]›[/] [moo.menu.text]{msg}[/]")


def print_ok(msg: str) -> None:
    console.print(f"[moo.ok]✓[/] {msg}")


def print_warn(msg: str) -> None:
    console.print(f"[moo.warn]✗[/] {msg}")


def print_info(msg: str) -> None:
    console.print(f"[moo.dim]·[/] [moo.subtitle]{msg}[/]")
