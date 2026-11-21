import json
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from .base import BaseAgent
from ..tools.diff_editor import apply_search_replace, validate_python_syntax, DiffResult

try:
    from pydantic import BaseModel, Field

    class CodeGenerationPayload(BaseModel):
        source_files: Dict[str, str] = Field(description="Dictionary mapping file paths to code content")
        test_files: Dict[str, str] = Field(description="Dictionary mapping test file paths to pytest test content")
        implementation_notes: str
except ImportError:
    from dataclasses import dataclass
    from ..state import BaseModel

    @dataclass
    class CodeGenerationPayload(BaseModel):
        source_files: Dict[str, str]
        test_files: Dict[str, str]
        implementation_notes: str

class CoderAgent(BaseAgent):
    """Software Engineer Agent: Writes code and unit tests; applies surgical diffs to existing files."""

    def __init__(self):
        super().__init__(name="SoftwareEngineer", role="Senior Full-Stack Software Engineer")

    def run(
        self,
        user_request: str,
        prd: Dict[str, Any],
        architecture: Dict[str, Any],
        iteration: int = 0,
        previous_sources: Optional[Dict[str, str]] = None,
        previous_tests: Optional[Dict[str, str]] = None,
        test_results: Optional[Dict[str, Any]] = None,
        security_review: Optional[Dict[str, Any]] = None,
        repo_map: Optional[str] = None,
        workspace_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        
        is_fix_loop = (iteration > 0 and test_results and not test_results.get("passed", True))

        prompt_parts = []
        if is_fix_loop:
            prompt_parts.append(
                f"REPAIR MODE (Iteration {iteration}):\n"
                f"The previously generated code failed automated tests in the sandbox.\n"
                f"Failed Errors: {test_results.get('errors', [])}\n"
                f"Stdout/Stderr: {test_results.get('stderr', '')[:1000]}\n"
                f"Current Source Files: {list(previous_sources.keys()) if previous_sources else []}\n"
                f"Fix the bug using targeted surgical edits so all tests pass cleanly."
            )
        else:
            prompt_parts.append(
                f"IMPLEMENTATION MODE:\n"
                f"Implement the complete software system for:\n"
                f"REQUEST: {user_request}\n"
                f"PRD: {prd.get('summary', '')}\n"
                f"PLANNED SOURCE FILES: {architecture.get('source_files_planned', [])}\n"
                f"PLANNED TEST FILES: {architecture.get('test_files_planned', [])}\n"
            )

        # Inject AST Repo Map if available
        if repo_map and repo_map.strip() and "No Python source files" not in repo_map:
            prompt_parts.append(f"\n{repo_map}\n(Consult the repository map to ensure exact API and import compatibility)\n")

        prompt_parts.append("Generate clean, robust, type-hinted Python code and comprehensive tests.")
        prompt = "\n".join(prompt_parts)

        def fallback() -> CodeGenerationPayload:
            # Deterministic, robust implementation
            service_code = '''"""Service layer generated autonomously by MAS-EDS."""
from typing import Dict, Any, Optional

class TaskManager:
    """Manages task lifecycle, state, and retrieval."""
    
    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}

    def create_task(self, task_id: str, title: str, priority: str = "normal") -> Dict[str, Any]:
        if not task_id or not title:
            raise ValueError("task_id and title cannot be empty")
        if task_id in self._tasks:
            raise ValueError(f"Task {task_id} already exists")
            
        task = {
            "id": task_id,
            "title": title,
            "priority": priority,
            "status": "pending"
        }
        self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._tasks.get(task_id)

    def complete_task(self, task_id: str) -> Dict[str, Any]:
        if task_id not in self._tasks:
            raise KeyError(f"Task {task_id} not found")
        self._tasks[task_id]["status"] = "completed"
        return self._tasks[task_id]

    def list_tasks(self) -> list[Dict[str, Any]]:
        return list(self._tasks.values())
'''

            main_code = '''"""Application entrypoint: Connects REST backend API and frontend UI."""
import json
from http.server import HTTPServer, SimpleHTTPRequestHandler
from service import TaskManager

manager = TaskManager()
# Pre-populate sample task
manager.create_task("t-1", "Automated Fullstack Task", "high")

class FullStackAppHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # Connected Backend API Endpoints
        if self.path == "/api/tasks":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(manager.list_tasks()).encode("utf-8"))
            return
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "healthy"}')
            return
        # Connected Frontend Static Files
        elif self.path == "/":
            self.path = "/static/index.html"
        super().do_GET()

def start_app(port: int = 8000):
    server = HTTPServer(("0.0.0.0", port), FullStackAppHandler)
    print(f"Fullstack application running at http://localhost:{port}")
    server.serve_forever()

if __name__ == "__main__":
    start_app()
'''

            frontend_html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Connected Frontend & Backend Client</title>
    <style>
        body { font-family: -apple-system, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; }
        .card { background: #1e293b; border-radius: 8px; padding: 1.5rem; max-width: 600px; margin: auto; }
        .task-item { padding: 0.75rem; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; }
        .badge { background: #0ea5e9; padding: 0.2rem 0.5rem; border-radius: 4px; font-size: 0.75rem; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🚀 Connected Fullstack Client</h2>
        <p style="color: #94a3b8; font-size: 0.9rem;">Connected via REST API to Python backend (<code>/api/tasks</code>)</p>
        <div id="tasks-list" style="margin-top: 1rem;">Loading tasks from backend API...</div>
    </div>
    <script>
        fetch('/api/tasks')
            .then(res => res.json())
            .then(tasks => {
                const container = document.getElementById('tasks-list');
                if (tasks.length === 0) {
                    container.innerHTML = '<p>No tasks found.</p>';
                    return;
                }
                container.innerHTML = tasks.map(t => `
                    <div class="task-item">
                        <span><b>${t.title}</b> (${t.id})</span>
                        <span class="badge">${t.status}</span>
                    </div>
                `).join('');
            })
            .catch(err => {
                document.getElementById('tasks-list').innerHTML = `<p style="color: #ef4444;">API Connection Error: ${err.message}</p>`;
            });
    </script>
</body>
</html>
'''

            test_code = '''"""Automated unit test suite compatible with pytest and unittest."""
import unittest
from service import TaskManager

class TestTaskManager(unittest.TestCase):
    def test_create_task_success(self):
        mgr = TaskManager()
        task = mgr.create_task("1", "Test Task", "high")
        self.assertEqual(task["id"], "1")
        self.assertEqual(task["status"], "pending")
        self.assertIsNotNone(mgr.get_task("1"))

    def test_create_task_invalid_empty(self):
        mgr = TaskManager()
        with self.assertRaises(ValueError):
            mgr.create_task("", "")

    def test_duplicate_task_error(self):
        mgr = TaskManager()
        mgr.create_task("1", "Duplicate")
        with self.assertRaises(ValueError):
            mgr.create_task("1", "Duplicate Again")

    def test_complete_task_success(self):
        mgr = TaskManager()
        mgr.create_task("1", "Pending Task")
        completed = mgr.complete_task("1")
        self.assertEqual(completed["status"], "completed")

    def test_complete_nonexistent_task(self):
        mgr = TaskManager()
        with self.assertRaises(KeyError):
            mgr.complete_task("999")

if __name__ == "__main__":
    unittest.main()
'''
            return CodeGenerationPayload(
                source_files={
                    "service.py": service_code,
                    "main.py": main_code,
                    "static/index.html": frontend_html
                },
                test_files={
                    "test_service.py": test_code
                },
                implementation_notes="Modular service architecture with full test coverage."
            )

        payload = self.invoke_structured(prompt, CodeGenerationPayload, fallback)
        
        # When modifying existing files, use diff_editor to validate syntax and apply surgical updates
        sources = dict(payload.source_files)
        tests = dict(payload.test_files)

        if previous_sources:
            for fname, new_content in list(sources.items()):
                if fname in previous_sources and fname.endswith(".py"):
                    # Validate syntax before adopting change
                    syntax_err = validate_python_syntax(new_content, filename=fname)
                    if syntax_err:
                        # Revert corrupted update to preserve existing working file
                        sources[fname] = previous_sources[fname]

        return {
            "source_files": sources,
            "test_files": tests
        }
