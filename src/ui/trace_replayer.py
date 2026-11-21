"""Interactive Trace Replayer stepping through .jsonl execution logs with Rich panels."""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from .compat import Console, Panel, Table, Syntax, Prompt, box, is_interactive, strip_markup, RICH_AVAILABLE
from ..config import get_trace_log_dir

console = Console()

def replay_trace(run_id: str, interactive: Optional[bool] = None) -> bool:
    """Steps through a recorded JSONL trace log and visualizes agent telemetry and diffs.

    Returns:
        True if trace was found and replayed, False otherwise.
    """
    trace_dir = get_trace_log_dir()
    trace_file = trace_dir / f"{run_id}.jsonl"
    if not trace_file.exists():
        # Try direct path
        direct = Path(run_id)
        if direct.exists() and direct.suffix == ".jsonl":
            trace_file = direct
        else:
            console.print(f"[bold red]Error:[/bold red] Trace log not found for run ID: [cyan]{run_id}[/cyan]")
            console.print(f"[dim]Looked in: {trace_file}[/dim]")
            available = list(trace_dir.glob("*.jsonl"))
            if available:
                console.print("\nAvailable trace logs:")
                for f in available[:5]:
                    console.print(f"  • {f.stem}")
            return False

    events: List[Dict[str, Any]] = []
    with open(trace_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass

    if not events:
        console.print(f"[yellow]Trace log for {run_id} contains no valid event records.[/yellow]")
        return False

    if interactive is None:
        interactive = is_interactive()

    # Calculate run summary
    tot_prompt = sum(e.get("prompt_tokens", 0) for e in events)
    tot_comp = sum(e.get("completion_tokens", 0) for e in events)
    final_cost = events[-1].get("cumulative_cost_usd", 0.0)

    summary_text = (
        f"[bold cyan]Run ID:[/bold cyan] {run_id}  |  "
        f"[bold cyan]Total Events:[/bold cyan] {len(events)}  |  "
        f"[bold cyan]Total Tokens:[/bold cyan] {tot_prompt + tot_comp} ({tot_prompt} in / {tot_comp} out)  |  "
        f"[bold cyan]Total Cost:[/bold cyan] [bold yellow]${final_cost:.4f}[/bold yellow]"
    )
    console.print()
    console.print(
        Panel(
            summary_text,
            title="[bold green]⏮️ MAS-EDS Interactive Trace Replay[/bold green]",
            border_style="green",
            box=box.ROUNDED
        )
    )

    auto_advance = not interactive

    for idx, event in enumerate(events, start=1):
        agent = event.get("agent", "Agent")
        etype = event.get("event_type", "event")
        ts = event.get("timestamp", "")[:19].replace("T", " ")
        p_tok = event.get("prompt_tokens", 0)
        c_tok = event.get("completion_tokens", 0)
        cost = event.get("cost_usd", 0.0)
        lat = event.get("latency_ms", 0.0)
        meta = event.get("metadata", {})

        # Header metrics
        badge_color = "magenta" if agent == "Orchestrator" else "cyan"
        title = f"[bold {badge_color}][Step {idx}/{len(events)}][/bold {badge_color}] {agent} • [bold]{etype}[/bold] [dim]({ts})[/dim]"

        body_parts = []
        if lat > 0 or p_tok > 0 or cost > 0:
            body_parts.append(
                f"[dim]Latency:[/dim] {lat:.0f}ms  |  "
                f"[dim]Cost:[/dim] ${cost:.6f}  |  "
                f"[dim]Tokens:[/dim] {p_tok} in / {c_tok} out"
            )

        # Transition metadata
        if "from_node" in meta and "to_node" in meta:
            body_parts.append(f"[bold blue]Transition:[/bold blue] {meta['from_node']} ➔ [bold green]{meta['to_node']}[/bold green]")

        if "reason" in meta:
            body_parts.append(f"[bold yellow]Note:[/bold yellow] {meta['reason']}")

        if "summary" in meta:
            body_parts.append(f"[bold]Summary:[/bold] {meta['summary']}")

        # Code Diff display
        diff_text = meta.get("diff")
        if diff_text:
            body_parts.append("\n[bold cyan]Code Diff Generated:[/bold cyan]")
            if RICH_AVAILABLE:
                body_parts.append(Syntax(diff_text, "diff", theme="monokai", line_numbers=True))
            else:
                body_parts.append(diff_text)

        panel_content = "\n".join(str(p) for p in body_parts) if body_parts else "[dim]No metadata recorded[/dim]"
        console.print(
            Panel(
                panel_content,
                title=title,
                border_style=badge_color,
                box=box.ROUNDED
            )
        )

        if not auto_advance:
            choice = Prompt.ask(
                "[dim]Advance replay[/dim]",
                choices=["", "a", "q"],
                default=""
            ).lower()
            if choice == "q":
                console.print("[dim]Replay paused by user.[/dim]\n")
                break
            elif choice == "a":
                auto_advance = True

    console.print("[bold green]Trace replay finished.[/bold green]\n")
    return True
