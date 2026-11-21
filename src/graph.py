"""LangGraph delivery workflow with SQLite checkpointing and human-in-the-loop interruption."""

import json
import sqlite3
from pathlib import Path
from typing import Literal, Dict, Any, Optional, List

from .state import AgentState, reduce_error_logs
from .config import get_workspace_dir, get_checkpoint_db_path, settings
from .context.repo_map import generate_repo_map
from .agents.base import active_run_id, TelemetryTracker
from .agents import (
    ProductAgent,
    ArchitectAgent,
    CoderAgent,
    QAAgent,
    SecurityAgent,
    DevOpsAgent
)

class SqliteCheckpointer:
    """Persistent SQLite checkpointer for saving and resuming graph execution states."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else get_checkpoint_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(str(self.db_path))

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def put(self, config: Dict[str, Any], state: Dict[str, Any]) -> None:
        thread_id = (
            config.get("configurable", {}).get("thread_id")
            or state.get("thread_id")
            or state.get("session_id")
        )
        if not thread_id:
            return

        # Sanitize state for JSON serialization
        clean_state = {}
        for k, v in state.items():
            if k.startswith("_"):
                clean_state[k] = v
                continue
            try:
                json.dumps(v, default=str)
                clean_state[k] = v
            except Exception:
                clean_state[k] = str(v)

        conn = self._get_conn()
        try:
            conn.execute(
                """
                INSERT INTO checkpoints (thread_id, state_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(thread_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(thread_id), json.dumps(clean_state, default=str))
            )
            conn.commit()
        finally:
            conn.close()

    def get(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            return None
        conn = self._get_conn()
        try:
            cursor = conn.execute("SELECT state_json FROM checkpoints WHERE thread_id = ?", (str(thread_id),))
            row = cursor.fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except Exception:
                    pass
        finally:
            conn.close()
        return None

    def list_checkpoints(self) -> List[str]:
        conn = self._get_conn()
        try:
            cursor = conn.execute("SELECT thread_id FROM checkpoints ORDER BY updated_at DESC")
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

END = "__end__"

class StateGraph:
    """Lightweight StateGraph engine with checkpointing and interruption gates."""

    def __init__(self, state_schema):
        self.state_schema = state_schema
        self.nodes: Dict[str, Any] = {}
        self.edges: Dict[str, str] = {}
        self.conditional_edges: Dict[str, Any] = {}
        self.entry_point: Optional[str] = None

    def add_node(self, name: str, func: Any):
        self.nodes[name] = func

    def set_entry_point(self, name: str):
        self.entry_point = name

    def add_edge(self, from_node: str, to_node: str):
        self.edges[from_node] = to_node

    def add_conditional_edges(self, from_node: str, router_func: Any, path_map: Dict[str, str]):
        self.conditional_edges[from_node] = (router_func, path_map)

    def compile(self, checkpointer=None, interrupt_before: Optional[List[str]] = None):
        class CompiledGraph:
            def __init__(self, parent, checkpointer, interrupt_before):
                self.parent = parent
                self.checkpointer = checkpointer
                self.interrupt_before = interrupt_before or []

            def invoke(self, initial_state: dict, config: Optional[dict] = None) -> dict:
                cfg = config or {}
                thread_id = (
                    cfg.get("configurable", {}).get("thread_id")
                    or initial_state.get("thread_id")
                    or initial_state.get("session_id", "default_thread")
                )

                # Resume from checkpointer if existing state is stored
                state = dict(initial_state)
                if self.checkpointer:
                    saved = self.checkpointer.get({"configurable": {"thread_id": thread_id}})
                    if saved:
                        saved_next = saved.get("_next_node")
                        is_explicit_resume = bool(
                            state.get("_resume")
                            or cfg.get("configurable", {}).get("resume")
                        )

                        # Resume if explicitly requested, or if saved state is in an interrupted/in-flight state
                        if (saved_next and saved_next != END) or is_explicit_resume:
                            merged = dict(saved)
                            for k, v in state.items():
                                if k == "status" and v in ("in_progress", "initialized") and saved.get("status"):
                                    continue
                                if v not in (None, "", [], {}):
                                    merged[k] = v
                            state = merged

                state["thread_id"] = thread_id
                run_id = state.get("run_id") or state.get("session_id") or thread_id
                active_run_id.set(run_id)

                current_node = state.get("_next_node") or self.parent.entry_point
                max_steps = 50
                steps = 0

                tracker = TelemetryTracker.get_tracker(run_id)
                on_step = cfg.get("on_step")

                while current_node and current_node != END and steps < max_steps:
                    steps += 1

                    # Human-in-the-Loop Interruption Check
                    if current_node in self.interrupt_before:
                        passed_interrupts = state.get("_passed_interrupts", [])
                        if current_node not in passed_interrupts:
                            state["_passed_interrupts"] = passed_interrupts + [current_node]
                            state["_next_node"] = current_node
                            state["status"] = f"interrupted_before_{current_node}"
                            if self.checkpointer:
                                self.checkpointer.put({"configurable": {"thread_id": thread_id}}, state)
                            tracker.record_transition(
                                from_node="orchestrator",
                                to_node=f"interrupt:{current_node}",
                                metadata={"reason": "HITL gate triggered"}
                            )
                            if on_step:
                                try:
                                    on_step(current_node, "interrupt", state)
                                except Exception:
                                    pass
                            return state

                    # Notify start
                    if on_step:
                        try:
                            on_step(current_node, "start", state)
                        except Exception:
                            pass

                    # Execute node function
                    fn = self.parent.nodes[current_node]
                    prev_node = current_node
                    updates = fn(state)
                    if updates:
                        state.update(updates)

                    state["cost_usd"] = tracker.cumulative_cost_usd
                    state["token_usage"] = {
                        "prompt_tokens": tracker.total_prompt_tokens,
                        "completion_tokens": tracker.total_completion_tokens,
                        "total_tokens": tracker.total_prompt_tokens + tracker.total_completion_tokens
                    }

                    if on_step:
                        try:
                            on_step(prev_node, "finish", state)
                        except Exception:
                            pass

                    # Determine routing
                    if current_node in self.parent.conditional_edges:
                        router_fn, path_map = self.parent.conditional_edges[current_node]
                        target = router_fn(state)
                        resolved = path_map.get(target, target)
                        current_node = resolved
                    elif current_node in self.parent.edges:
                        current_node = self.parent.edges[current_node]
                    else:
                        current_node = None

                    state["_next_node"] = current_node

                    # Record node transition telemetry and save checkpoint
                    diff_val = state.get("diff_summary") if prev_node == "coder" else None
                    tracker.record_transition(
                        from_node=prev_node,
                        to_node=str(current_node),
                        diff=diff_val,
                        metadata={"step": steps, "status": state.get("status")}
                    )

                    if self.checkpointer:
                        self.checkpointer.put({"configurable": {"thread_id": thread_id}}, state)

                return state

        return CompiledGraph(self, checkpointer, interrupt_before)

# Initialize agent instances
product_agent = ProductAgent()
architect_agent = ArchitectAgent()
coder_agent = CoderAgent()
qa_agent = QAAgent()
security_agent = SecurityAgent()
devops_agent = DevOpsAgent()

def node_product(state: AgentState) -> Dict[str, Any]:
    """Node 1: Product requirements & PRD generation."""
    user_request = state.get("user_request", "")
    prd = product_agent.run(user_request)
    
    logs = state.get("audit_log", [])
    logs.append({"agent": "ProductAgent", "event": "PRD generated", "summary": prd.get("summary", "")})
    
    return {
        "prd": prd,
        "audit_log": logs,
        "status": "spec_ready"
    }

def node_architect(state: AgentState) -> Dict[str, Any]:
    """Node 2: System architecture & file decomposition."""
    session_id = state.get("session_id", "default_session")
    workspace_dir = get_workspace_dir(session_id)
    repo_map = generate_repo_map(workspace_dir)

    user_request = state.get("user_request", "")
    prd = state.get("prd", {})
    architecture = architect_agent.run(user_request, prd, repo_map=repo_map)
    
    logs = state.get("audit_log", [])
    logs.append({
        "agent": "ArchitectAgent",
        "event": "Architecture plan generated",
        "summary": architecture.get("system_overview", "")
    })
    
    return {
        "architecture": architecture,
        "audit_log": logs,
        "status": "architecture_ready"
    }

def node_coder(state: AgentState) -> Dict[str, Any]:
    """Node 3: Code and test generation or repair."""
    session_id = state.get("session_id", "default_session")
    workspace_dir = get_workspace_dir(session_id)
    repo_map = generate_repo_map(workspace_dir)

    user_request = state.get("user_request", "")
    prd = state.get("prd", {})
    architecture = state.get("architecture", {})
    iteration = state.get("iteration_count", 0)
    prev_sources = state.get("source_files", {})
    prev_tests = state.get("test_files", {})
    test_results = state.get("test_results")
    sec_review = state.get("security_review")

    code_payload = coder_agent.run(
        user_request=user_request,
        prd=prd,
        architecture=architecture,
        iteration=iteration,
        previous_sources=prev_sources,
        previous_tests=prev_tests,
        test_results=test_results,
        security_review=sec_review,
        repo_map=repo_map,
        workspace_path=workspace_dir
    )

    import difflib
    diff_chunks = []
    if prev_sources:
        for fname, new_code in code_payload["source_files"].items():
            old_code = prev_sources.get(fname, "")
            if old_code != new_code:
                diff_lines = list(difflib.unified_diff(
                    old_code.splitlines(keepends=True),
                    new_code.splitlines(keepends=True),
                    fromfile=f"a/{fname}",
                    tofile=f"b/{fname}"
                ))
                if diff_lines:
                    diff_chunks.append("".join(diff_lines))
    if not diff_chunks and code_payload.get("source_files"):
        first_fname = next(iter(code_payload["source_files"].keys()))
        first_code = code_payload["source_files"][first_fname]
        diff_chunks.append(f"--- /dev/null\n+++ b/{first_fname}\n" + "".join(f"+{l}\n" for l in first_code.splitlines()[:20]))

    diff_summary = "\n".join(diff_chunks)

    logs = state.get("audit_log", [])
    logs.append({
        "agent": "CoderAgent",
        "event": f"Code updated (iteration {iteration})",
        "summary": f"{len(code_payload['source_files'])} sources, {len(code_payload['test_files'])} test files"
    })

    return {
        "source_files": code_payload["source_files"],
        "test_files": code_payload["test_files"],
        "diff_summary": diff_summary,
        "audit_log": logs,
        "status": "code_generated"
    }

def node_qa(state: AgentState) -> Dict[str, Any]:
    """Node 4: Automated sandbox test execution and error compaction."""
    session_id = state.get("session_id", "default_session")
    workspace_dir = get_workspace_dir(session_id)
    source_files = state.get("source_files", {})
    test_files = state.get("test_files", {})
    use_docker = (settings.sandbox_mode == "docker")

    test_results = qa_agent.run(
        workspace_dir=workspace_dir,
        source_files=source_files,
        test_files=test_files,
        use_docker=use_docker
    )

    logs = state.get("audit_log", [])
    logs.append({
        "agent": "QAAgent",
        "event": "Sandbox tests executed",
        "summary": test_results.get("summary", "")
    })

    # Update and compact error logs to prevent LLM prompt bloat
    current_error_logs = state.get("error_logs", [])
    new_error_entries = list(test_results.get("errors", []))
    if not test_results.get("passed") and test_results.get("stderr"):
        new_error_entries.append(test_results["stderr"])
    compacted_error_logs = reduce_error_logs(current_error_logs, new_error_entries)

    return {
        "test_results": test_results,
        "error_logs": compacted_error_logs,
        "audit_log": logs,
        "status": "qa_passed" if test_results.get("passed") else "qa_failed"
    }

def node_security(state: AgentState) -> Dict[str, Any]:
    """Node 5: Multi-layer security audit and quality gate review."""
    session_id = state.get("session_id", "default_session")
    workspace_dir = get_workspace_dir(session_id)
    source_files = state.get("source_files", {})

    sec_review = security_agent.run(source_files, workspace_path=workspace_dir)

    logs = state.get("audit_log", [])
    logs.append({
        "agent": "SecurityAgent",
        "event": "Security review completed",
        "summary": sec_review.get("summary", "")
    })

    return {
        "security_review": sec_review,
        "audit_report": sec_review,
        "audit_log": logs,
        "status": "security_approved" if sec_review.get("approved") else "security_rejected"
    }

def node_devops(state: AgentState) -> Dict[str, Any]:
    """Node 6: Packaging, Docker containerization, CI/CD, and Git delivery."""
    session_id = state.get("session_id", "default_session")
    workspace_dir = get_workspace_dir(session_id)
    prd = state.get("prd", {})
    arch = state.get("architecture", {})
    test_results = state.get("test_results", {})
    sec_review = state.get("security_review", {})

    app_name = f"app-{session_id[:8]}"
    entrypoint = arch.get("execution_entrypoint", "main.py")

    delivery_artifacts = devops_agent.run(
        workspace_dir=workspace_dir,
        app_name=app_name,
        entrypoint=entrypoint,
        prd_summary=prd.get("summary", ""),
        test_summary=test_results.get("summary", ""),
        security_summary=sec_review.get("summary", "")
    )

    logs = state.get("audit_log", [])
    logs.append({
        "agent": "DevOpsAgent",
        "event": "Delivery artifacts produced & Git branch committed",
        "summary": delivery_artifacts.get("release_notes", "")
    })

    return {
        "delivery_artifacts": delivery_artifacts,
        "audit_log": logs,
        "status": "ready_for_delivery"
    }

def node_human_gate(state: AgentState) -> Dict[str, Any]:
    """Node 7: Final Human-in-the-Loop approval gate."""
    auto_approve = settings.auto_approve_deployment or state.get("human_approved", False)
    logs = state.get("audit_log", [])
    
    if auto_approve:
        logs.append({
            "agent": "Gatekeeper",
            "event": "Delivery approved",
            "summary": "Deployment approved for release."
        })
        return {
            "status": "delivered",
            "human_approved": True,
            "audit_log": logs
        }
    else:
        logs.append({
            "agent": "Gatekeeper",
            "event": "Pending human sign-off",
            "summary": "Artifacts ready. Awaiting user confirmation to release."
        })
        return {
            "status": "pending_human_signoff",
            "human_approved": False,
            "audit_log": logs
        }

# --- Routing Functions ---

def route_after_qa(state: AgentState) -> Literal["security_review", "coder", "__end__"]:
    test_results = state.get("test_results", {})
    iteration = state.get("iteration_count", 0)
    max_iter = state.get("max_iterations", settings.max_repair_iterations)

    if test_results.get("passed", False):
        return "security_review"
    
    if iteration < max_iter:
        state["iteration_count"] = iteration + 1
        return "coder"
    
    return END

def route_after_security(state: AgentState) -> Literal["devops_delivery", "coder", "__end__"]:
    review = state.get("security_review", {})
    iteration = state.get("iteration_count", 0)
    max_iter = state.get("max_iterations", settings.max_repair_iterations)

    if review.get("approved", False):
        return "devops_delivery"
    
    if iteration < max_iter:
        state["iteration_count"] = iteration + 1
        return "coder"
    
    return END

def create_delivery_graph(
    checkpointer=None,
    interrupt_before: Optional[List[str]] = None,
    interactive_approval: bool = False
):
    """Builds and compiles the StateGraph with checkpointing and HITL interruption gates."""
    builder = StateGraph(AgentState)

    # Add Nodes
    builder.add_node("product_spec", node_product)
    builder.add_node("architect", node_architect)
    builder.add_node("coder", node_coder)
    builder.add_node("qa_test", node_qa)
    builder.add_node("security_review", node_security)
    builder.add_node("devops_delivery", node_devops)
    builder.add_node("human_gate", node_human_gate)

    # Add Edges
    builder.set_entry_point("product_spec")
    builder.add_edge("product_spec", "architect")
    builder.add_edge("architect", "coder")
    builder.add_edge("coder", "qa_test")

    # Conditional branching
    builder.add_conditional_edges(
        "qa_test",
        route_after_qa,
        {
            "security_review": "security_review",
            "coder": "coder",
            END: END
        }
    )

    builder.add_conditional_edges(
        "security_review",
        route_after_security,
        {
            "devops_delivery": "devops_delivery",
            "coder": "coder",
            END: END
        }
    )

    builder.add_edge("devops_delivery", "human_gate")
    builder.add_edge("human_gate", END)

    # Configure checkpointer and interruptions
    if checkpointer is None:
        checkpointer = SqliteCheckpointer(get_checkpoint_db_path())

    effective_interrupts = list(interrupt_before or [])
    if interactive_approval and not effective_interrupts:
        effective_interrupts = ["architect", "devops"]

    return builder.compile(checkpointer=checkpointer, interrupt_before=effective_interrupts)
