import os
import sys
import json
import uuid
import threading
import io
import zipfile
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from .config import settings, get_workspace_dir
from .graph import create_delivery_graph
from .state import AgentState

# In-memory store for active sessions
active_sessions: Dict[str, Dict[str, Any]] = {}
WEB_DIR = Path(__file__).parent / "web"

def execute_swarm_task(session_id: str, prompt: str, sandbox: str, max_retries: int, auto_approve: bool):
    """Background worker executing the LangGraph swarm."""
    settings.sandbox_mode = sandbox
    settings.max_repair_iterations = max_retries
    settings.auto_approve_deployment = auto_approve

    initial_state: AgentState = {
        "session_id": session_id,
        "user_request": prompt,
        "iteration_count": 0,
        "max_iterations": max_retries,
        "status": "in_progress",
        "human_approved": auto_approve,
        "audit_log": [{"agent": "Orchestrator", "event": "Swarm initialized", "summary": prompt}]
    }
    active_sessions[session_id] = dict(initial_state)

    try:
        graph = create_delivery_graph()
        final_state = graph.invoke(initial_state)
        active_sessions[session_id] = dict(final_state)
    except Exception as e:
        active_sessions[session_id]["status"] = "error"
        active_sessions[session_id]["error_message"] = str(e)
        active_sessions[session_id].get("audit_log", []).append({
            "agent": "System", "event": "Execution error", "summary": str(e)
        })

def execute_revision_task(session_id: str, feedback: str):
    """Executes an interactive human-in-the-loop revision cycle."""
    session = active_sessions.get(session_id)
    if not session:
        return

    session["status"] = "in_progress"
    session["human_approved"] = False
    audit_log = session.setdefault("audit_log", [])
    audit_log.append({
        "agent": "HumanGate",
        "event": "User revision feedback received",
        "summary": feedback
    })

    workspace_dir = get_workspace_dir(session_id)
    prd = session.get("prd", {})
    arch = session.get("architecture", {})
    prev_sources = session.get("source_files", {})
    prev_tests = session.get("test_files", {})
    iteration = session.get("iteration_count", 0) + 1
    session["iteration_count"] = iteration

    try:
        from .agents import CoderAgent, QAAgent, SecurityAgent, DevOpsAgent
        from .context.repo_map import generate_repo_map

        # 1. Coder implements user feedback
        repo_map = generate_repo_map(workspace_dir)
        coder = CoderAgent()
        code_payload = coder.run(
            user_request=f"{session.get('user_request', '')}\n[USER REVISION]: {feedback}",
            prd=prd,
            architecture=arch,
            iteration=iteration,
            previous_sources=prev_sources,
            previous_tests=prev_tests,
            repo_map=repo_map,
            workspace_path=workspace_dir
        )
        session["source_files"] = code_payload["source_files"]
        session["test_files"] = code_payload["test_files"]
        audit_log.append({
            "agent": "CoderAgent",
            "event": f"Code revised based on user feedback (iteration {iteration})",
            "summary": f"Addressed: {feedback[:80]}"
        })

        # 2. QA runs tests in sandbox
        qa = QAAgent()
        test_results = qa.run(
            workspace_dir=workspace_dir,
            source_files=session["source_files"],
            test_files=session["test_files"],
            use_docker=(settings.sandbox_mode == "docker")
        )
        session["test_results"] = test_results
        audit_log.append({
            "agent": "QAAgent",
            "event": "Sandbox tests re-verified",
            "summary": test_results.get("summary", "")
        })

        # 3. Security AST scan
        sec = SecurityAgent()
        sec_review = sec.run(session["source_files"])
        session["security_review"] = sec_review
        audit_log.append({
            "agent": "SecurityAgent",
            "event": "Security review re-evaluated",
            "summary": sec_review.get("summary", "")
        })

        # 4. DevOps generates commit and delivery package
        devops = DevOpsAgent()
        app_name = f"app-{session_id[:8]}"
        entrypoint = arch.get("execution_entrypoint", "main.py")
        delivery_artifacts = devops.run(
            workspace_dir=workspace_dir,
            app_name=app_name,
            entrypoint=entrypoint,
            prd_summary=prd.get("summary", ""),
            test_summary=test_results.get("summary", ""),
            security_summary=sec_review.get("summary", "")
        )
        session["delivery_artifacts"] = delivery_artifacts
        session["status"] = "pending_human_signoff"
        audit_log.append({
            "agent": "DevOpsAgent",
            "event": "Git branch updated with new revision commit",
            "summary": delivery_artifacts.get("release_notes", "")
        })

    except Exception as e:
        session["status"] = "error"
        session["error_message"] = str(e)
        audit_log.append({
            "agent": "System", "event": "Revision error", "summary": str(e)
        })

class SwarmHTTPHandler(SimpleHTTPRequestHandler):
    """Unified HTTP handler serving frontend assets and REST API endpoints."""

    def do_GET(self):
        clean_path = self.path.split("?")[0]
        # 1. API: Session Status
        if clean_path.startswith("/api/sessions/"):
            parts = clean_path.strip("/").split("/")
            if len(parts) >= 3:
                session_id = parts[2]
                session_data = active_sessions.get(session_id)
                if session_data:
                    self._send_json(200, session_data)
                else:
                    self._send_json(404, {"error": "Session not found"})
                return

        # 2. API: List Sessions
        elif clean_path == "/api/sessions":
            sessions_summary = [
                {"session_id": sid, "status": data.get("status"), "prompt": data.get("user_request")}
                for sid, data in active_sessions.items()
            ]
            self._send_json(200, {"sessions": sessions_summary})
            return

        # 3. API: Workspace Files Explorer
        elif clean_path.startswith("/api/files/"):
            session_id = clean_path.strip("/").split("/")[-1]
            workspace_dir = get_workspace_dir(session_id)
            files_dict = {}

            if workspace_dir.exists():
                for root, _, files in os.walk(workspace_dir):
                    if ".git" in root or "__pycache__" in root:
                        continue
                    for file in files:
                        full_path = Path(root) / file
                        rel_path = full_path.relative_to(workspace_dir).as_posix()
                        try:
                            files_dict[rel_path] = full_path.read_text(encoding="utf-8")
                        except Exception:
                            files_dict[rel_path] = "<binary or unreadable file>"

            self._send_json(200, files_dict)
            return

        # 4. API: Git Diff of latest commit & changes
        elif clean_path.startswith("/api/diff/"):
            session_id = clean_path.strip("/").split("/")[-1]
            workspace_dir = get_workspace_dir(session_id)
            if workspace_dir.exists():
                from .tools.git_tools import GitManager
                git_mgr = GitManager(workspace_dir)
                diff_text = git_mgr.get_diff_summary()
                self._send_json(200, {"session_id": session_id, "diff": diff_text})
            else:
                self._send_json(404, {"error": "Workspace not found"})
            return

        # 5. API: Download Project as ZIP archive
        elif clean_path.startswith("/api/download/"):
            session_id = clean_path.strip("/").split("/")[-1]
            workspace_dir = get_workspace_dir(session_id)
            if workspace_dir.exists():
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                    for root, _, files in os.walk(workspace_dir):
                        if ".git" in root or "__pycache__" in root:
                            continue
                        for file in files:
                            file_path = Path(root) / file
                            rel_path = file_path.relative_to(workspace_dir)
                            zip_file.write(file_path, arcname=str(rel_path))
                
                zip_data = zip_buffer.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="mas-project-{session_id}.zip"')
                self.send_header("Content-Length", str(len(zip_data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(zip_data)
                return
            else:
                self._send_json(404, {"error": "Workspace not found"})
                return

        # 6. Health Check Probes (Render, Kubernetes, Cloud Run)
        elif clean_path in ("/health", "/api/health"):
            self._send_json(200, {
                "status": "healthy",
                "service": "MAS-EDS Backend",
                "active_sessions": len(active_sessions),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            return

        # 7. Frontend: Serve Dashboard HTML & Favicon
        elif clean_path in ("/", "/index.html", ""):
            index_path = WEB_DIR / "index.html"
            if index_path.exists():
                content = index_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)
                return

        elif clean_path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # Fallback to standard handler
        super().do_GET()

    def do_POST(self):
        clean_path = self.path.split("?")[0]

        # 1. API: Run Swarm Task
        if clean_path == "/api/run":
            content_length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(content_length)
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                self._send_json(400, {"error": "Invalid JSON"})
                return

            prompt = body.get("prompt", "")
            sandbox = body.get("sandbox", "local")
            max_retries = int(body.get("max_retries", 3))
            auto_approve = bool(body.get("auto_approve", False))
            session_id = body.get("session_id") or f"task_{uuid.uuid4().hex[:8]}"

            # Launch background worker
            thread = threading.Thread(
                target=execute_swarm_task,
                args=(session_id, prompt, sandbox, max_retries, auto_approve),
                daemon=True
            )
            thread.start()

            self._send_json(200, {"session_id": session_id, "status": "started"})
            return

        # 2. API: Approve Delivery Gate
        elif clean_path.startswith("/api/sessions/") and clean_path.endswith("/approve"):
            parts = self.path.strip("/").split("/")
            session_id = parts[2]
            session = active_sessions.get(session_id)
            if session:
                session["status"] = "delivered"
                session["human_approved"] = True
                session.get("audit_log", []).append({
                    "agent": "Gatekeeper", "event": "Approved by User via Web UI", "summary": "Release deployment granted."
                })
                self._send_json(200, {"session_id": session_id, "status": "delivered"})
            else:
                self._send_json(404, {"error": "Session not found"})
            return

        # 3. API: Human Revision Feedback Loop
        elif clean_path.startswith("/api/sessions/") and clean_path.endswith("/revise"):
            parts = self.path.strip("/").split("/")
            session_id = parts[2]
            content_length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(content_length)
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                self._send_json(400, {"error": "Invalid JSON"})
                return

            feedback = body.get("feedback", "")
            if not feedback:
                self._send_json(400, {"error": "Feedback text is required"})
                return

            thread = threading.Thread(
                target=execute_revision_task,
                args=(session_id, feedback),
                daemon=True
            )
            thread.start()

            self._send_json(200, {"session_id": session_id, "status": "revision_started"})
            return

        self._send_json(404, {"error": "Endpoint not found"})

    def _send_json(self, status: int, data: Any):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

def start_server(port: Optional[int] = None, host: Optional[str] = None):
    """Starts the MAS-EDS server connecting Frontend and Backend with automatic port recovery."""
    env_port = os.getenv("PORT")
    if port is None:
        port = int(env_port) if env_port else 8080

    if host is None:
        # On Render, Heroku, Cloud Run, or Docker, bind to 0.0.0.0
        if os.getenv("RENDER") or os.getenv("PORT") or os.getenv("DOCKER"):
            host = "0.0.0.0"
        else:
            host = os.getenv("HOST", "127.0.0.1")

    # In cloud environments with assigned PORT, prioritize the specified port
    if os.getenv("RENDER") or os.getenv("PORT"):
        candidate_ports = [port]
    else:
        candidate_ports = [port, 8080, 5000, 3000, 8081, 8888]

    # Remove duplicates preserving order
    seen = set()
    candidate_ports = [p for p in candidate_ports if not (p in seen or seen.add(p))]

    httpd = None
    selected_port = None

    for p in candidate_ports:
        try:
            server_address = (host, p)
            httpd = HTTPServer(server_address, SwarmHTTPHandler)
            selected_port = p
            break
        except OSError:
            continue

    if not httpd:
        print(f"Error: Could not bind to any candidate port {candidate_ports}")
        return

    print(f"\n========================================================")
    print(f"⚡ MAS-EDS Web Console & API Server Running")
    print(f"Host:               {host}")
    print(f"Connected Frontend: http://{host}:{selected_port}")
    print(f"Connected API:      http://{host}:{selected_port}/api")
    print(f"Health Probe:       http://{host}:{selected_port}/health")
    print(f"========================================================\n", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()

if __name__ == "__main__":
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    start_server(port=port_arg)
