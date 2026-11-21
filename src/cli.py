"""Autonomous Multi-Agent Software Engineering & Continuous Delivery System (MAS-EDS) CLI."""

import sys
import os
import uuid
import argparse
from pathlib import Path
from typing import Optional

from .config import settings, get_workspace_dir
from .graph import create_delivery_graph
from .state import AgentState, reduce_error_logs
from .ui import (
    LiveDashboard,
    prompt_hitl_gate,
    interactive_runs_menu,
    replay_trace,
    is_interactive,
    Console,
    Panel,
    Table,
    box
)

console = Console()

def resolve_interactive(interactive: Optional[bool], auto_approve: bool) -> bool:
    """Resolves interactive HITL mode: defaults to True if TTY is attached, unless bypassed by auto_approve."""
    if auto_approve:
        return False
    if interactive is not None:
        return interactive
    return is_interactive()

def _run_lifecycle(
    prompt: str,
    session_id: Optional[str] = None,
    sandbox: str = "local",
    max_retries: int = 3,
    auto_approve: bool = False,
    resume: Optional[str] = None,
    interactive: Optional[bool] = None
) -> dict:
    effective_interactive = resolve_interactive(interactive, auto_approve)
    thread_id = resume or session_id or f"task_{uuid.uuid4().hex[:8]}"
    if not session_id:
        session_id = thread_id

    settings.sandbox_mode = sandbox
    settings.max_repair_iterations = max_retries
    settings.auto_approve_deployment = auto_approve

    initial_state: AgentState = {
        "session_id": session_id,
        "thread_id": thread_id,
        "user_request": prompt or "",
        "iteration_count": 0,
        "max_iterations": max_retries,
        "status": "initialized",
        "human_approved": auto_approve,
        "audit_log": []
    }
    if resume:
        initial_state["_resume"] = True

    dashboard = LiveDashboard(
        thread_id=thread_id,
        user_request=prompt or f"Resumed execution for {thread_id}",
        session_id=session_id
    )

    def on_step(node_id: str, event_type: str, state_snapshot: dict):
        dashboard.on_node_event(node_id, event_type, state_snapshot)

    graph = create_delivery_graph(interactive_approval=effective_interactive)
    config = {
        "configurable": {"thread_id": thread_id, "resume": bool(resume)},
        "on_step": on_step
    }

    dashboard.start()
    current_state = graph.invoke(initial_state, config=config)

    # Human-in-the-Loop Interruption Loop
    while current_state.get("status", "").startswith("interrupted_before_"):
        gate_name = current_state.get("status", "").replace("interrupted_before_", "")
        dashboard.stop()

        action, feedback = prompt_hitl_gate(gate_name, current_state)

        if action == "approve":
            dashboard.start()
            current_state["status"] = "in_progress"
            current_state = graph.invoke(current_state, config=config)

        elif action == "edit":
            dashboard.start()
            if gate_name == "architect":
                # Route back to product_spec to revise PRD with feedback
                passed = [p for p in current_state.get("_passed_interrupts", []) if p != "architect"]
                current_state["_passed_interrupts"] = passed
                current_state["_next_node"] = "product_spec"
                current_state["user_request"] = f"{current_state.get('user_request', '')}\n[User Feedback]: {feedback}"
                current_state["status"] = "in_progress"
            elif gate_name == "devops":
                # Route back to coder to implement revision
                passed = [p for p in current_state.get("_passed_interrupts", []) if p != "devops"]
                current_state["_passed_interrupts"] = passed
                current_state["_next_node"] = "coder"
                current_state["user_request"] = f"{current_state.get('user_request', '')}\n[User Feedback]: {feedback}"
                current_state["error_logs"] = reduce_error_logs(
                    current_state.get("error_logs", []),
                    [f"User Revision Request: {feedback}"]
                )
                current_state["status"] = "in_progress"

            current_state = graph.invoke(current_state, config=config)

        elif action == "quit":
            dashboard.stop()
            console.print(f"\n[bold yellow]Execution paused.[/bold yellow] State checkpointed to thread_id '[bold cyan]{thread_id}[/bold cyan]'.")
            console.print(f"To resume later, run: [bold green]swe run --resume {thread_id}[/bold green]\n")
            return current_state

    dashboard.stop()

    # Terminal summary presentation
    final_status = current_state.get("status")
    workspace_dir = get_workspace_dir(session_id)
    delivery = current_state.get("delivery_artifacts", {})

    console.print()
    if final_status == "delivered":
        console.rule("[bold green]✨ DELIVERY COMPLETE: SYSTEM PACKAGED & VERIFIED[/bold green]")
        console.print(f"[bold]Workspace Location:[/bold] {workspace_dir.resolve()}")
        if delivery:
            console.print(f"[dim]{delivery.get('release_notes', '')}[/dim]")
    elif final_status == "pending_human_signoff":
        console.rule("[bold yellow]⏸️ PENDING HUMAN SIGN-OFF[/bold yellow]")
        console.print("Artifacts verified in sandbox. Workspace ready for final inspection.")
    else:
        console.rule(f"[bold cyan]Status: {final_status}[/bold cyan]")

    return current_state


# Try loading typer for CLI interface, falling back to argparse
try:
    import typer

    app = typer.Typer(
        name="swe",
        help="Autonomous Multi-Agent Software Engineering & Continuous Delivery System (MAS-EDS)",
        add_completion=False
    )

    @app.command()
    def run(
        prompt: Optional[str] = typer.Argument(None, help="Software engineering task or requirement specification"),
        session_id: Optional[str] = typer.Option(None, "--session-id", "-s", help="Unique session ID"),
        sandbox: str = typer.Option("local", "--sandbox", help="Sandbox execution mode ('local' or 'docker')"),
        max_retries: int = typer.Option(3, "--max-retries", "-r", help="Maximum test repair iterations"),
        auto_approve: bool = typer.Option(False, "--auto-approve", "-y", "--yes", help="Auto-approve production deployment gate"),
        resume: Optional[str] = typer.Option(None, "--resume", help="Resume interrupted run by thread_id"),
        interactive: Optional[bool] = typer.Option(None, "--interactive/--no-interactive", "-i", help="Enable/disable HITL interruption gates")
    ):
        """Execute the full autonomous software engineering and delivery lifecycle."""
        _run_lifecycle(prompt or "", session_id, sandbox, max_retries, auto_approve, resume, interactive)

    @app.command()
    def runs():
        """List saved checkpoints and execution traces with interactive resume / replay."""
        choice = interactive_runs_menu()
        if choice:
            action, target_id = choice
            if action == "resume":
                _run_lifecycle(prompt="", resume=target_id)
            elif action == "replay":
                replay_trace(target_id)

    @app.command()
    def replay(
        run_id: str = typer.Argument(..., help="Run / Thread ID of the trace log to replay")
    ):
        """Interactively step through an execution trace log."""
        replay_trace(run_id)

    @app.command()
    def serve(
        port: int = typer.Option(8080, "--port", "-p", help="Port to serve web console on"),
        host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host address")
    ):
        """Start the Web Console and API server connecting frontend and backend."""
        from .server import start_server
        start_server(port=port, host=host)

    @app.command()
    def test():
        """Verify local installation and run internal test suite."""
        import unittest
        suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
        runner = unittest.TextTestRunner(verbosity=2)
        res = runner.run(suite)
        sys.exit(0 if res.wasSuccessful() else 1)

except ImportError:
    # Pure-Python argparse CLI runner
    def main():
        parser = argparse.ArgumentParser(
            prog="swe",
            description="Autonomous Multi-Agent Software Engineering & Continuous Delivery System (MAS-EDS)"
        )
        subparsers = parser.add_subparsers(dest="command")

        # 1. run command
        run_parser = subparsers.add_parser("run", help="Run multi-agent SWE delivery lifecycle")
        run_parser.add_argument("prompt", nargs="?", default="", help="Software engineering task prompt")
        run_parser.add_argument("--session-id", "-s", default=None)
        run_parser.add_argument("--sandbox", default="local", choices=["local", "docker"])
        run_parser.add_argument("--max-retries", "-r", type=int, default=3)
        run_parser.add_argument("--auto-approve", "-y", "--yes", action="store_true", dest="auto_approve")
        run_parser.add_argument("--resume", default=None, help="Resume execution by thread_id")
        run_parser.add_argument("--interactive", "-i", action="store_true", default=None)
        run_parser.add_argument("--no-interactive", action="store_false", dest="interactive")

        # 2. runs command
        subparsers.add_parser("runs", help="List saved checkpoints and execution traces")

        # 3. replay command
        replay_parser = subparsers.add_parser("replay", help="Interactively step through an execution trace log")
        replay_parser.add_argument("run_id", help="Run / Thread ID of the trace log to replay")

        # 4. serve command
        serve_parser = subparsers.add_parser("serve", help="Start web console and API server")
        serve_parser.add_argument("--port", "-p", type=int, default=8080)
        serve_parser.add_argument("--host", "-H", default="127.0.0.1")

        # 5. test command
        subparsers.add_parser("test", help="Run test suite")

        # Allow passing prompt without explicit 'run' subcommand
        args_list = sys.argv[1:]
        if args_list and args_list[0] not in ("run", "runs", "replay", "serve", "test", "-h", "--help"):
            args_list.insert(0, "run")

        args = parser.parse_args(args_list)

        if args.command == "run":
            _run_lifecycle(
                prompt=args.prompt,
                session_id=args.session_id,
                sandbox=args.sandbox,
                max_retries=args.max_retries,
                auto_approve=args.auto_approve,
                resume=args.resume,
                interactive=args.interactive
            )
        elif args.command == "runs":
            choice = interactive_runs_menu()
            if choice:
                action, target_id = choice
                if action == "resume":
                    _run_lifecycle(prompt="", resume=target_id)
                elif action == "replay":
                    replay_trace(target_id)
        elif args.command == "replay":
            replay_trace(args.run_id)
        elif args.command == "serve":
            from .server import start_server
            start_server(port=args.port, host=args.host)
        elif args.command == "test":
            import unittest
            suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
            runner = unittest.TextTestRunner(verbosity=2)
            res = runner.run(suite)
            sys.exit(0 if res.wasSuccessful() else 1)
        else:
            parser.print_help()

    app = main

if __name__ == "__main__":
    if callable(app):
        app()
