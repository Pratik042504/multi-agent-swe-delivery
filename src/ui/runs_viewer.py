"""Interactive table viewer for saved SQLite checkpoints and JSONL execution traces."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from .compat import Console, Table, Prompt, box, is_interactive, strip_markup
from ..config import get_checkpoint_db_path, get_trace_log_dir

console = Console()

def load_all_runs() -> List[Dict[str, Any]]:
    """Loads and aggregates run metadata from SQLite checkpoints and JSONL traces."""
    runs_map: Dict[str, Dict[str, Any]] = {}

    # 1. Inspect SQLite checkpoints
    db_path = get_checkpoint_db_path()
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                cursor = conn.execute("SELECT thread_id, state_json, updated_at FROM checkpoints ORDER BY updated_at DESC")
                for tid, state_str, updated_at in cursor.fetchall():
                    try:
                        state = json.loads(state_str)
                    except Exception:
                        state = {}

                    prompt = state.get("user_request") or state.get("prompt") or "(No prompt)"
                    status = state.get("status") or "unknown"
                    cost = state.get("cost_usd", 0.0)

                    runs_map[tid] = {
                        "run_id": tid,
                        "prompt": prompt,
                        "status": status,
                        "cost_usd": float(cost),
                        "tokens": 0,
                        "duration_sec": 0.0,
                        "updated_at": updated_at or "",
                        "has_checkpoint": True,
                        "has_trace": False
                    }
            finally:
                conn.close()
        except Exception:
            pass

    # 2. Inspect JSONL trace logs
    trace_dir = get_trace_log_dir()
    if trace_dir.exists():
        for trace_file in trace_dir.glob("*.jsonl"):
            rid = trace_file.stem
            events = []
            try:
                with open(trace_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                events.append(json.loads(line))
                            except Exception:
                                pass
            except Exception:
                continue

            if not events:
                continue

            total_prompt_tok = sum(e.get("prompt_tokens", 0) for e in events)
            total_comp_tok = sum(e.get("completion_tokens", 0) for e in events)
            tot_tokens = total_prompt_tok + total_comp_tok
            last_cost = events[-1].get("cumulative_cost_usd", 0.0)

            # Calculate duration
            duration = 0.0
            t_first_str = events[0].get("timestamp")
            t_last_str = events[-1].get("timestamp")
            if t_first_str and t_last_str:
                try:
                    t0 = datetime.fromisoformat(t_first_str.replace("Z", "+00:00"))
                    t1 = datetime.fromisoformat(t_last_str.replace("Z", "+00:00"))
                    duration = max((t1 - t0).total_seconds(), 0.0)
                except Exception:
                    pass

            first_prompt = events[0].get("metadata", {}).get("user_request") or ""

            if rid in runs_map:
                runs_map[rid]["has_trace"] = True
                runs_map[rid]["tokens"] = tot_tokens
                if last_cost > 0:
                    runs_map[rid]["cost_usd"] = float(last_cost)
                if duration > 0:
                    runs_map[rid]["duration_sec"] = duration
                if not runs_map[rid]["prompt"] and first_prompt:
                    runs_map[rid]["prompt"] = first_prompt
            else:
                runs_map[rid] = {
                    "run_id": rid,
                    "prompt": first_prompt or "(From trace)",
                    "status": "completed_trace",
                    "cost_usd": float(last_cost),
                    "tokens": tot_tokens,
                    "duration_sec": duration,
                    "updated_at": t_last_str[:19] if t_last_str else "",
                    "has_checkpoint": False,
                    "has_trace": True
                }

    # Sort by updated_at descending
    runs = list(runs_map.values())
    runs.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return runs

def display_runs_table(runs: List[Dict[str, Any]]) -> None:
    """Renders formatted Rich table of all runs."""
    if not runs:
        console.print("[yellow]No saved runs or traces found in .swe_cache/[/yellow]")
        return

    table = Table(
        title="Saved Checkpoints & Execution Traces",
        box=box.ROUNDED,
        expand=True
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Run / Thread ID", style="bold cyan", width=22)
    table.add_column("Task Prompt", style="white", ratio=4)
    table.add_column("Status", style="bold", ratio=2)
    table.add_column("Cost ($)", justify="right", style="bold yellow", width=10)
    table.add_column("Tokens", justify="right", style="dim", width=8)
    table.add_column("Duration", justify="right", style="cyan", width=9)
    table.add_column("Last Updated", style="dim", width=16)

    for idx, r in enumerate(runs, start=1):
        st = r["status"]
        if st == "delivered":
            st_badge = "[bold green]DELIVERED[/bold green]"
        elif st.startswith("interrupted_before_"):
            gate = st.replace("interrupted_before_", "")
            st_badge = f"[bold cyan]PAUSED ({gate})[/bold cyan]"
        elif st == "pending_human_signoff":
            st_badge = "[bold yellow]PENDING SIGN-OFF[/bold yellow]"
        elif st == "error":
            st_badge = "[bold red]ERROR[/bold red]"
        else:
            st_badge = f"[blue]{st[:15]}[/blue]"

        prompt_summary = r["prompt"].replace("\n", " ")
        if len(prompt_summary) > 45:
            prompt_summary = prompt_summary[:42] + "..."

        dur_str = f"{r['duration_sec']:.1f}s" if r["duration_sec"] > 0 else "-"
        cost_str = f"${r['cost_usd']:.4f}"
        tok_str = str(r["tokens"]) if r["tokens"] > 0 else "-"
        updated_str = str(r["updated_at"])[:16].replace("T", " ")

        table.add_row(
            str(idx),
            r["run_id"],
            prompt_summary,
            st_badge,
            cost_str,
            tok_str,
            dur_str,
            updated_str
        )

    console.print(table)

def interactive_runs_menu() -> Optional[Tuple[str, str]]:
    """Displays runs and prompts user to select a run to resume or replay.

    Returns:
        (action, target_id) where action is 'resume', 'replay', or None.
    """
    runs = load_all_runs()
    display_runs_table(runs)

    if not runs or not is_interactive():
        return None

    console.print("\n[dim]Options: Enter a run number (e.g. '1'), 'resume <id>', 'replay <id>', or 'q' to quit[/dim]")
    choice = Prompt.ask("[bold cyan]Action[/bold cyan]", default="q").strip()

    if choice.lower() in ("q", "quit", "exit"):
        return None

    # Handle numeric selection
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(runs):
            selected = runs[idx - 1]
            rid = selected["run_id"]
            action = Prompt.ask(
                f"Selected run [bold cyan]{rid}[/bold cyan]. Action",
                choices=["resume", "replay", "cancel"],
                default="resume" if selected["has_checkpoint"] else "replay"
            )
            if action in ("resume", "replay"):
                return (action, rid)
            return None

    # Handle explicit command
    parts = choice.split(maxsplit=1)
    if len(parts) == 2:
        cmd, target = parts[0].lower(), parts[1].strip()
        # Check if target is index
        if target.isdigit():
            idx = int(target)
            if 1 <= idx <= len(runs):
                target = runs[idx - 1]["run_id"]

        if cmd in ("resume", "replay", "play"):
            action = "replay" if cmd in ("replay", "play") else "resume"
            return (action, target)

    return None
