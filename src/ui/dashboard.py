"""Live Split-Pane TUI Dashboard using Rich Layout, Live, Panel, and Syntax."""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

from .compat import (
    Console,
    Live,
    Layout,
    Panel,
    Table,
    Syntax,
    Text,
    box,
    is_interactive,
    strip_markup,
    RICH_AVAILABLE
)
from ..config import settings, get_workspace_dir
from ..agents.base import TelemetryTracker

PIPELINE_STAGES = [
    ("product_spec", "1. ProductSpec"),
    ("architect", "2. Architect"),
    ("coder", "3. Coder"),
    ("qa_test", "4. Linter/QA"),
    ("security_review", "5. Security"),
    ("devops_delivery", "6. DevOps"),
]

class LiveDashboard:
    """Manages real-time Rich split-pane dashboard and non-TTY streaming logs."""

    def __init__(
        self,
        thread_id: str,
        user_request: str,
        session_id: Optional[str] = None,
        max_budget_usd: Optional[float] = None
    ):
        self.thread_id = thread_id
        self.session_id = session_id or thread_id
        self.user_request = user_request
        self.max_budget_usd = max_budget_usd or getattr(settings, "max_budget_usd", 5.0)
        self.workspace_dir = get_workspace_dir(self.session_id)
        self.start_time = time.time()
        self.interactive = is_interactive()

        # Telemetry and state caches
        self.node_states: Dict[str, Dict[str, Any]] = {
            node_id: {
                "label": label,
                "status": "PENDING",
                "latency_ms": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "start_time": None
            }
            for node_id, label in PIPELINE_STAGES
        }
        self.active_node: Optional[str] = None
        self.activity_log: List[str] = []
        self.latest_diff: Optional[str] = None
        self.latest_test_summary: Optional[Dict[str, Any]] = None
        self.latest_security_summary: Optional[Dict[str, Any]] = None

        self.console = Console()
        self.live: Optional[Live] = None
        self.layout: Optional[Layout] = None

        if self.interactive:
            self._setup_layout()

    def _setup_layout(self) -> None:
        """Constructs split-pane Layout structure."""
        self.layout = Layout()
        self.layout.split_column(
            Layout(name="header", size=4),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3)
        )
        self.layout["body"].split_row(
            Layout(name="pipeline", ratio=4),
            Layout(name="activity", ratio=6)
        )

    def start(self) -> None:
        """Starts live TUI rendering or logs initial non-TTY banner."""
        if self.interactive and self.layout:
            self._render_view()
            self.live = Live(
                self.layout,
                console=self.console,
                refresh_per_second=4,
                screen=False
            )
            self.live.start()
        else:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] [MAS-EDS] Initialized session={self.thread_id} workspace={self.workspace_dir}")
            print(f"[{ts}] [MAS-EDS] Request: {self.user_request[:80]}")
            print(f"[{ts}] [MAS-EDS] Budget: ${self.max_budget_usd:.2f} | Mode: {settings.sandbox_mode}")

    def stop(self) -> None:
        """Stops the live rendering loop and releases terminal cursor."""
        if self.live:
            try:
                self.live.stop()
            except Exception:
                pass
            self.live = None

    def on_node_event(self, node_id: str, event_type: str, state: Dict[str, Any]) -> None:
        """Event sink invoked on every node transition and status change."""
        now = time.time()
        stage_info = self.node_states.get(node_id)
        iteration = state.get("iteration_count", 0)
        max_iter = state.get("max_iterations", 3)
        tracker = TelemetryTracker.get_tracker(self.thread_id)

        # Update per-agent telemetry
        if stage_info:
            if event_type == "start":
                self.active_node = node_id
                stage_info["start_time"] = now
                stage_info["status"] = "RUNNING"
                msg = f"Started node {stage_info['label']}"
                self._append_log(msg)

            elif event_type == "finish":
                if stage_info.get("start_time"):
                    stage_info["latency_ms"] = (now - stage_info["start_time"]) * 1000

                # Match status depending on test/security results
                if node_id == "qa_test":
                    test_res = state.get("test_results") or {}
                    self.latest_test_summary = test_res
                    if test_res.get("passed"):
                        stage_info["status"] = "PASSED"
                    elif iteration < max_iter:
                        stage_info["status"] = f"RETRY {iteration}/{max_iter}"
                    else:
                        stage_info["status"] = "BLOCKED"
                elif node_id == "security_review":
                    sec = state.get("security_review") or {}
                    self.latest_security_summary = sec
                    if sec.get("approved"):
                        stage_info["status"] = "PASSED"
                    else:
                        stage_info["status"] = "BLOCKED"
                else:
                    stage_info["status"] = "PASSED"

                msg = f"Completed node {stage_info['label']} in {stage_info['latency_ms']:.0f}ms"
                self._append_log(msg)

            elif event_type == "interrupt":
                stage_info["status"] = "PAUSED (HITL)"
                self._append_log(f"Interrupted at Human-in-the-Loop gate: {node_id}")

        # Update diff stream if coder finished
        if node_id == "coder" and event_type == "finish":
            sources = state.get("source_files", {})
            if sources:
                fnames = list(sources.keys())
                preview_file = fnames[0]
                content = sources[preview_file]
                lines = content.splitlines()[:25]
                self.latest_diff = f"# {preview_file} (showing {len(lines)} lines)\n" + "\n".join(lines)

        # Pull latest token usage from tracker
        if tracker.events and stage_info:
            recent = [e for e in tracker.events if e.get("metadata", {}).get("from_node") == node_id]
            if recent:
                last_ev = recent[-1]
                stage_info["prompt_tokens"] = last_ev.get("prompt_tokens", 0)
                stage_info["completion_tokens"] = last_ev.get("completion_tokens", 0)

        self.refresh()

    def _append_log(self, message: str) -> None:
        """Appends a timestamped log to the activity stream."""
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {message}"
        self.activity_log.append(entry)
        if len(self.activity_log) > 20:
            self.activity_log.pop(0)

        if not self.interactive:
            print(f"[MAS-EDS] {entry}")

    def refresh(self) -> None:
        """Re-renders and flushes dashboard layout if interactive."""
        if self.interactive and self.live and self.layout:
            self._render_view()
            try:
                self.live.update(self.layout, refresh=True)
            except Exception:
                pass

    def _render_view(self) -> None:
        """Updates internal Layout panels with current telemetry."""
        if not self.layout:
            return

        elapsed_sec = int(time.time() - self.start_time)
        mm, ss = divmod(elapsed_sec, 60)
        elapsed_str = f"{mm:02d}:{ss:02d}"

        tracker = TelemetryTracker.get_tracker(self.thread_id)
        cost_usd = tracker.cumulative_cost_usd
        cost_pct = min((cost_usd / max(self.max_budget_usd, 0.01)) * 100, 100.0)

        provider = getattr(settings, "llm_provider", "heuristic")
        model = getattr(settings, "model_name", "standard")

        # 1. Header Bar
        header_text = (
            f"[bold cyan]Thread:[/bold cyan] {self.thread_id}  |  "
            f"[bold cyan]Model:[/bold cyan] {provider}:{model}  |  "
            f"[bold cyan]Elapsed:[/bold cyan] {elapsed_str}  |  "
            f"[bold cyan]Cost:[/bold cyan] [bold yellow]${cost_usd:.4f}[/bold yellow] / ${self.max_budget_usd:.2f} ({cost_pct:.1f}%)\n"
            f"[dim]Workspace: {str(self.workspace_dir)}[/dim]"
        )
        self.layout["header"].update(
            Panel(
                header_text,
                title="[bold green]🚀 MAS-EDS Real-Time Agent Operations[/bold green]",
                border_style="cyan",
                box=box.ROUNDED
            )
        )

        # 2. Left Panel: Pipeline & Retry Loop
        pipe_table = Table(show_header=True, box=box.MINIMAL, expand=True)
        pipe_table.add_column("Agent / Node", style="bold", ratio=4)
        pipe_table.add_column("Status", ratio=3)
        pipe_table.add_column("Latency", justify="right", ratio=2)
        pipe_table.add_column("Tokens", justify="right", ratio=2)

        for node_id, info in self.node_states.items():
            st = info["status"]
            if st == "RUNNING":
                badge = "[bold yellow]RUNNING ⟳[/bold yellow]"
            elif st == "PASSED":
                badge = "[bold green]PASSED ✓[/bold green]"
            elif "RETRY" in st:
                badge = f"[bold orange3]{st} ↺[/bold orange3]"
            elif st == "BLOCKED":
                badge = "[bold red]BLOCKED ✗[/bold red]"
            elif "PAUSED" in st:
                badge = "[bold cyan]PAUSED ⏸[/bold cyan]"
            else:
                badge = "[dim]PENDING[/dim]"

            latency = f"{info['latency_ms']:.0f}ms" if info["latency_ms"] > 0 else "-"
            tokens = str(info["prompt_tokens"] + info["completion_tokens"]) if (info["prompt_tokens"] + info["completion_tokens"]) > 0 else "-"
            pipe_table.add_row(info["label"], badge, latency, tokens)

        self.layout["pipeline"].update(
            Panel(
                pipe_table,
                title="[bold blue]SDLC State Machine[/bold blue]",
                border_style="blue",
                box=box.ROUNDED
            )
        )

        # 3. Right Panel: Live Activity & Diff Stream
        activity_content = []
        if self.latest_diff:
            activity_content.append("[bold cyan]Code Stream / Source Preview:[/bold cyan]")
            if RICH_AVAILABLE:
                activity_content.append(Syntax(self.latest_diff, "python", theme="monokai", line_numbers=True))
            else:
                activity_content.append(self.latest_diff)

        if self.latest_test_summary:
            tsum = self.latest_test_summary
            st = "[bold green]PASSED[/bold green]" if tsum.get("passed") else "[bold red]FAILED[/bold red]"
            activity_content.append(f"\n[bold]Sandbox Verification:[/bold] {st} ({tsum.get('passed_tests', 0)}/{tsum.get('total_tests', 0)} tests)")

        if self.latest_security_summary:
            sec = self.latest_security_summary
            score = sec.get("quality_score", 10.0)
            findings_count = len(sec.get("findings", []))
            activity_content.append(f"[bold]Security Gate:[/bold] Score: {score}/10.0 | Findings: {findings_count}")

        # Recent log stream
        activity_content.append("\n[bold]Event Log:[/bold]")
        activity_content.extend(self.activity_log[-6:] if self.activity_log else ["[dim]Awaiting agent transitions...[/dim]"])

        right_panel_body = "\n".join(str(c) for c in activity_content)
        self.layout["activity"].update(
            Panel(
                right_panel_body,
                title="[bold magenta]Live Activity & Telemetry[/bold magenta]",
                border_style="magenta",
                box=box.ROUNDED
            )
        )

        # 4. Footer Bar
        self.layout["footer"].update(
            Panel(
                "[dim]Commands: [bold]swe runs[/bold] | [bold]swe replay <id>[/bold] | [bold]Ctrl+C[/bold] to interrupt & checkpoint[/dim]",
                border_style="dim",
                box=box.ROUNDED
            )
        )
