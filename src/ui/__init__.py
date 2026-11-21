"""MAS-EDS Terminal UI and Rich interaction subsystem."""

from .compat import (
    Console,
    Live,
    Layout,
    Panel,
    Table,
    Syntax,
    Text,
    Prompt,
    Confirm,
    box,
    is_interactive,
    strip_markup,
    RICH_AVAILABLE
)
from .dashboard import LiveDashboard
from .hitl import prompt_hitl_gate
from .runs_viewer import load_all_runs, display_runs_table, interactive_runs_menu
from .trace_replayer import replay_trace

__all__ = [
    "Console",
    "Live",
    "Layout",
    "Panel",
    "Table",
    "Syntax",
    "Text",
    "Prompt",
    "Confirm",
    "box",
    "is_interactive",
    "strip_markup",
    "RICH_AVAILABLE",
    "LiveDashboard",
    "prompt_hitl_gate",
    "load_all_runs",
    "display_runs_table",
    "interactive_runs_menu",
    "replay_trace"
]
