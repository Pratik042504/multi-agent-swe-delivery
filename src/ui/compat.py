"""Compatibility layer for Rich UI components with graceful pure-Python fallback."""

import os
import sys
import re
import shutil
from typing import Any, List, Dict, Optional, Union

def is_interactive() -> bool:
    """Checks whether the current session is an interactive TTY and not CI."""
    if os.environ.get("CI") or os.environ.get("NO_COLOR") or os.environ.get("DEBIAN_FRONTEND") == "noninteractive":
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False

def strip_markup(text: str) -> str:
    """Removes Rich-style tags such as [bold green]...[/bold green] from text."""
    if not isinstance(text, str):
        text = str(text)
    return re.sub(r"\[/?[a-zA-Z0-9_# -]+\]", "", text)

try:
    import rich
    from rich.console import Console
    from rich.live import Live
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.syntax import Syntax
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    import rich.box as box

    RICH_AVAILABLE = True

except ImportError:
    RICH_AVAILABLE = False

    class _Box:
        ROUNDED = "ROUNDED"
        HEAVY = "HEAVY"
        DOUBLE = "DOUBLE"
        ASCII = "ASCII"
        MINIMAL = "MINIMAL"

    box = _Box()

    class Text:
        """Lightweight text wrapper supporting styles and plain text extraction."""
        def __init__(self, text: str = "", style: str = ""):
            self._text = str(text)
            self._style = style

        @property
        def plain(self) -> str:
            return strip_markup(self._text)

        def append(self, text: str, style: str = "") -> "Text":
            self._text += str(text)
            return self

        def __str__(self) -> str:
            return strip_markup(self._text)

        def __repr__(self) -> str:
            return f"Text({self._text!r})"

    class Syntax:
        """Lightweight syntax highlighter fallback."""
        def __init__(
            self,
            code: str,
            lexer: str = "python",
            theme: str = "monokai",
            line_numbers: bool = False,
            word_wrap: bool = False
        ):
            self.code = code
            self.lexer = lexer
            self.line_numbers = line_numbers

        def __str__(self) -> str:
            lines = self.code.splitlines()
            if not self.line_numbers:
                return "\n".join(lines)
            digits = len(str(len(lines)))
            formatted = []
            for idx, line in enumerate(lines, start=1):
                formatted.append(f"{idx:>{digits}} | {line}")
            return "\n".join(formatted)

    class Table:
        """Pure-Python table renderer for CLI grids."""
        def __init__(
            self,
            title: str = "",
            border_style: str = "",
            show_header: bool = True,
            show_edge: bool = True,
            box: Any = None,
            expand: bool = False
        ):
            self.title = title
            self.border_style = border_style
            self.show_header = show_header
            self.show_edge = show_edge
            self.columns: List[Dict[str, Any]] = []
            self.rows: List[List[str]] = []

        def add_column(
            self,
            header: str = "",
            style: str = "",
            justify: str = "left",
            width: Optional[int] = None,
            ratio: Optional[int] = None,
            no_wrap: bool = False
        ) -> None:
            self.columns.append({
                "header": header,
                "style": style,
                "justify": justify,
                "width": width
            })

        def add_row(self, *cells: Any, style: str = "") -> None:
            self.rows.append([strip_markup(str(c)) for c in cells])

        def __str__(self) -> str:
            if not self.columns and not self.rows:
                return ""

            col_count = max(len(self.columns), max((len(r) for r in self.rows), default=0))
            col_widths = [0] * col_count

            # Determine column widths
            for idx, col in enumerate(self.columns):
                h_len = len(strip_markup(col.get("header", "")))
                col_widths[idx] = max(col_widths[idx], h_len)

            for row in self.rows:
                for idx, cell in enumerate(row):
                    if idx < col_count:
                        first_line = cell.splitlines()[0] if cell.splitlines() else ""
                        col_widths[idx] = max(col_widths[idx], len(first_line))

            for idx, col in enumerate(self.columns):
                if col.get("width"):
                    col_widths[idx] = col["width"]
                else:
                    col_widths[idx] = max(col_widths[idx], 4)

            lines = []
            sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"

            if self.title:
                total_w = sum(col_widths) + (len(col_widths) - 1) * 3 + 4
                t_str = f" {strip_markup(self.title)} "
                lines.append("=" * total_w)
                lines.append(f"{t_str:^{total_w}}")
                lines.append("=" * total_w)

            if self.show_header and self.columns:
                lines.append(sep)
                headers = []
                for idx, col in enumerate(self.columns):
                    w = col_widths[idx]
                    h = strip_markup(col.get("header", ""))
                    headers.append(f"{h:<{w}}")
                lines.append("| " + " | ".join(headers) + " |")
                lines.append(sep)

            for row in self.rows:
                padded = []
                for idx in range(col_count):
                    val = row[idx] if idx < len(row) else ""
                    first_line = val.splitlines()[0] if val.splitlines() else ""
                    w = col_widths[idx]
                    padded.append(f"{first_line:<{w}}")
                lines.append("| " + " | ".join(padded) + " |")

            lines.append(sep)
            return "\n".join(lines)

    class Panel:
        """Pure-Python boxed panel renderer."""
        def __init__(
            self,
            renderable: Any,
            title: str = "",
            subtitle: str = "",
            border_style: str = "",
            box: Any = None,
            expand: bool = True
        ):
            self.renderable = renderable
            self.title = title
            self.subtitle = subtitle
            self.border_style = border_style
            self.expand = expand

        def __str__(self) -> str:
            content_str = str(self.renderable)
            raw_lines = [strip_markup(line) for line in content_str.splitlines()]
            if not raw_lines:
                raw_lines = [""]

            term_width = min(shutil.get_terminal_size((80, 24)).columns, 100)
            max_line_len = max((len(l) for l in raw_lines), default=20)
            target_w = max(max_line_len + 4, len(strip_markup(self.title)) + 6, 40)
            if self.expand:
                target_w = max(target_w, min(term_width - 2, 90))

            inner_w = target_w - 4
            lines = []

            # Top border with title
            if self.title:
                t = f" {strip_markup(self.title)} "
                rem = inner_w - len(t)
                top = "┌─" + t + ("─" * max(rem, 0)) + "─┐"
            else:
                top = "┌" + ("─" * (inner_w + 2)) + "┐"
            lines.append(top)

            for line in raw_lines:
                lines.append(f"│ {line:<{inner_w}} │")

            # Bottom border with subtitle
            if self.subtitle:
                sub = f" {strip_markup(self.subtitle)} "
                rem = inner_w - len(sub)
                bot = "└─" + sub + ("─" * max(rem, 0)) + "─┘"
            else:
                bot = "└" + ("─" * (inner_w + 2)) + "┘"
            lines.append(bot)

            return "\n".join(lines)

    class Layout:
        """Lightweight split-pane Layout manager."""
        def __init__(self, renderable: Any = None, name: Optional[str] = None, ratio: int = 1, size: Optional[int] = None):
            self.name = name
            self.renderable = renderable
            self.ratio = ratio
            self.size = size
            self.split_direction: Optional[str] = None  # "row" or "column"
            self.children: List["Layout"] = []

        def split_row(self, *layouts: "Layout") -> None:
            self.split_direction = "row"
            self.children = list(layouts)

        def split_column(self, *layouts: "Layout") -> None:
            self.split_direction = "column"
            self.children = list(layouts)

        def update(self, renderable: Any) -> None:
            self.renderable = renderable

        def __getitem__(self, name: str) -> "Layout":
            if self.name == name:
                return self
            for child in self.children:
                try:
                    return child[name]
                except KeyError:
                    pass
            raise KeyError(f"Layout pane '{name}' not found")

        def __setitem__(self, name: str, renderable: Any) -> None:
            target = self[name]
            target.update(renderable)

        def __str__(self) -> str:
            if not self.children:
                return str(self.renderable or "")

            if self.split_direction == "column":
                return "\n".join(str(c) for c in self.children if str(c).strip())

            # Row split: side by side
            child_strs = [str(c).splitlines() for c in self.children]
            if len(child_strs) == 2:
                left_lines, right_lines = child_strs[0], child_strs[1]
                max_lines = max(len(left_lines), len(right_lines))
                left_w = max((len(l) for l in left_lines), default=35)
                right_w = max((len(l) for l in right_lines), default=45)

                combined = []
                for idx in range(max_lines):
                    l_part = left_lines[idx] if idx < len(left_lines) else ""
                    r_part = right_lines[idx] if idx < len(right_lines) else ""
                    combined.append(f"{l_part:<{left_w}}  {r_part:<{right_w}}")
                return "\n".join(combined)

            return "\n".join(str(c) for c in self.children)

    class Console:
        """Lightweight console supporting rule, print, and styled status."""
        def __init__(self, *args, **kwargs):
            pass

        @property
        def is_terminal(self) -> bool:
            return is_interactive()

        @property
        def width(self) -> int:
            return shutil.get_terminal_size((80, 24)).columns

        def print(self, *args: Any, **kwargs: Any) -> None:
            if not args:
                print()
                return
            rendered = " ".join(strip_markup(str(a)) for a in args)
            print(rendered)

        def rule(self, title: str = "", characters: str = "─") -> None:
            w = min(self.width, 80)
            if not title:
                print(characters * w)
            else:
                t = f" {strip_markup(title)} "
                rem = max(w - len(t), 0)
                left = rem // 2
                right = rem - left
                print(f"{characters * left}{t}{characters * right}")

    class Live:
        """Lightweight context manager for terminal screen updates."""
        def __init__(
            self,
            renderable: Any = None,
            console: Optional[Console] = None,
            refresh_per_second: float = 4.0,
            auto_refresh: bool = True,
            screen: bool = False
        ):
            self.renderable = renderable
            self.console = console or Console()
            self._active = False
            self._interactive = is_interactive()

        def __enter__(self) -> "Live":
            self.start()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb) -> None:
            self.stop()

        def start(self) -> None:
            self._active = True
            if self._interactive and self.renderable:
                self.refresh()

        def stop(self) -> None:
            self._active = False

        def update(self, renderable: Any, refresh: bool = True) -> None:
            self.renderable = renderable
            if refresh and self._active and self._interactive:
                self.refresh()

        def refresh(self) -> None:
            if not self._active or not self.renderable:
                return
            if self._interactive:
                # Clear terminal screen in ANSI terminal
                print("\033[2J\033[H", end="", flush=True)
                print(strip_markup(str(self.renderable)), flush=True)

    class Prompt:
        """Interactive terminal prompter."""
        @classmethod
        def ask(
            cls,
            prompt: str,
            choices: Optional[List[str]] = None,
            default: Optional[str] = None
        ) -> str:
            prompt_str = strip_markup(prompt)
            if choices:
                choices_str = "/".join(choices)
                if default:
                    display = f"{prompt_str} [{choices_str}] (default: {default}): "
                else:
                    display = f"{prompt_str} [{choices_str}]: "
            elif default:
                display = f"{prompt_str} (default: {default}): "
            else:
                display = f"{prompt_str}: "

            if not is_interactive():
                return default or (choices[0] if choices else "")

            while True:
                try:
                    val = input(display).strip()
                except (EOFError, KeyboardInterrupt):
                    return default or ""

                if not val and default is not None:
                    return default

                if not choices:
                    return val

                matching = [c for c in choices if c.lower() == val.lower()]
                if matching:
                    return matching[0]

                print(f"Invalid choice '{val}'. Options: {', '.join(choices)}")

    class Confirm:
        """Interactive boolean prompter."""
        @classmethod
        def ask(cls, prompt: str, default: bool = True) -> bool:
            choices = ["y", "n"] if default else ["n", "y"]
            ans = Prompt.ask(prompt, choices=["y", "n"], default="y" if default else "n")
            return ans.lower().startswith("y")
