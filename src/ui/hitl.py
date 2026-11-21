"""Interactive Human-in-the-Loop (HITL) prompt handlers for LangGraph gates."""

from typing import Dict, Any, Tuple
from .compat import Console, Panel, Table, Prompt, box, is_interactive, strip_markup

console = Console()

def prompt_hitl_gate(gate_name: str, state: Dict[str, Any]) -> Tuple[str, str]:
    """Pauses execution at an SDLC gate and prompts user for approval, revision, or exit.

    Returns:
        (action, feedback) where action is 'approve', 'edit', or 'quit'.
    """
    if not is_interactive():
        # Non-TTY / automated environment
        auto = state.get("human_approved", False)
        return ("approve", "") if auto else ("quit", "")

    console.print()
    if gate_name == "architect":
        prd = state.get("prd", {})
        console.rule("[bold cyan]⏸️ HUMAN-IN-THE-LOOP GATE: SPECIFICATION REVIEW[/bold cyan]")
        
        table = Table(title="Product Requirements Review", box=box.ROUNDED, expand=True)
        table.add_column("Property", style="bold yellow", width=20)
        table.add_column("Details", style="white")

        table.add_row("PRD Title", prd.get("title", "N/A"))
        table.add_row("PRD Summary", prd.get("summary", "N/A"))
        
        features = prd.get("features", [])
        feat_str = "\n".join(f"• {f.get('name', 'Feature')}: {len(f.get('acceptance_criteria', []))} ACs" for f in features)
        table.add_row("Features", feat_str or "No features specified")

        nfrs = prd.get("non_functional_requirements", [])
        table.add_row("NFRs", ", ".join(nfrs) if nfrs else "None")

        console.print(table)
        console.print("[dim]Review the autonomous specification above before architectural layout begins.[/dim]\n")

    elif gate_name == "devops":
        console.rule("[bold cyan]⏸️ HUMAN-IN-THE-LOOP GATE: PRE-RELEASE CODE & SECURITY AUDIT[/bold cyan]")
        
        sources = state.get("source_files", {})
        tests = state.get("test_files", {})
        test_res = state.get("test_results", {})
        sec = state.get("security_review", {})

        table = Table(title="Pre-Delivery Quality & Security Audit", box=box.ROUNDED, expand=True)
        table.add_column("Audit Metric", style="bold yellow", width=22)
        table.add_column("Verification Result", style="white")

        table.add_row("Generated Codebase", f"{len(sources)} source files, {len(tests)} test files")
        
        qa_status = "[bold green]PASSED[/bold green]" if test_res.get("passed") else "[bold red]FAILED[/bold red]"
        qa_detail = f"{qa_status} ({test_res.get('passed_tests', 0)}/{test_res.get('total_tests', 0)} passed)"
        table.add_row("Sandbox Verification", qa_detail)

        sec_score = sec.get("quality_score", 10.0)
        findings = sec.get("findings", [])
        sec_detail = f"Quality Score: {sec_score}/10.0 | Severe Findings: {len(findings)}"
        table.add_row("Security AST & Scans", sec_detail)

        console.print(table)
        console.print("[dim]Review code quality and sandbox test results before git branch commitment & release packaging.[/dim]\n")

    else:
        console.rule(f"[bold cyan]⏸️ HUMAN-IN-THE-LOOP GATE: {gate_name.upper()}[/bold cyan]")

    choice = Prompt.ask(
        "[bold green][A][/bold green]pprove & Continue  |  "
        "[bold yellow][E][/bold yellow]dit Feedback & Retry  |  "
        "[bold red][Q][/bold red]uit (Save Checkpoint)",
        choices=["A", "E", "Q", "a", "e", "q"],
        default="A"
    ).upper()

    if choice == "A":
        return ("approve", "")
    elif choice == "E":
        feedback = Prompt.ask("[bold yellow]Enter your revision feedback or modification request[/bold yellow]")
        return ("edit", feedback.strip())
    else:
        return ("quit", "")
