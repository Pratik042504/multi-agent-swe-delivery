import unittest
import json
import zipfile
import io
from pathlib import Path
from src.server import (
    active_sessions,
    execute_swarm_task,
    execute_revision_task,
    WEB_DIR
)
from src.config import get_workspace_dir
from src.tools.git_tools import GitManager

class TestSwarmServerLogic(unittest.TestCase):
    def test_frontend_assets_exist(self):
        index_file = WEB_DIR / "index.html"
        self.assertTrue(index_file.exists(), "index.html must exist in src/web/")
        content = index_file.read_text(encoding="utf-8")
        
        # Verify critical frontend UI components and API links
        self.assertIn("MAS-EDS", content)
        self.assertIn("/api/run", content)
        self.assertIn("/api/sessions", content)
        self.assertIn("/api/files", content)
        self.assertIn("/api/diff", content)
        self.assertIn("approveDelivery", content)
        self.assertIn("submitRevision", content)
        self.assertIn("download-zip-btn", content)

    def test_swarm_execution_task_lifecycle(self):
        session_id = "test_server_task_123"
        prompt = "Build a microservice with health endpoint"
        
        # Execute swarm worker task synchronously for testing
        execute_swarm_task(
            session_id=session_id,
            prompt=prompt,
            sandbox="local",
            max_retries=3,
            auto_approve=True
        )

        self.assertIn(session_id, active_sessions)
        session = active_sessions[session_id]
        self.assertEqual(session.get("status"), "delivered")
        self.assertTrue(session.get("human_approved"))
        self.assertIsNotNone(session.get("prd"))
        self.assertIsNotNone(session.get("delivery_artifacts"))

    def test_human_revision_feedback_cycle(self):
        session_id = "test_revision_cycle_456"
        # Seed an initial session
        execute_swarm_task(
            session_id=session_id,
            prompt="Initial API service",
            sandbox="local",
            max_retries=3,
            auto_approve=False
        )
        self.assertEqual(active_sessions[session_id]["status"], "pending_human_signoff")

        # Execute human revision
        feedback = "Add input sanitization and rate limit headers"
        execute_revision_task(session_id, feedback)

        session = active_sessions[session_id]
        self.assertEqual(session.get("status"), "pending_human_signoff")
        self.assertGreaterEqual(session.get("iteration_count", 0), 1)
        self.assertTrue(any("sanitization" in log.get("summary", "") for log in session.get("audit_log", [])))

    def test_git_diff_and_zip_generation(self):
        session_id = "test_diff_zip_789"
        workspace_dir = get_workspace_dir(session_id)
        
        # Initialize Git repo and sample files
        git_mgr = GitManager(workspace_dir)
        (workspace_dir / "app.py").write_text("print('hello world')\n", encoding="utf-8")
        git_mgr.commit_changes("feat: add app.py")
        
        # Verify git diff
        diff_text = git_mgr.get_diff_summary()
        self.assertIsNotNone(diff_text)
        self.assertTrue("app.py" in diff_text or "nothing to commit" in diff_text)

        # Verify zip archive creation
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in workspace_dir.glob("*.py"):
                zip_file.write(file_path, arcname=file_path.name)
        
        self.assertGreater(len(zip_buffer.getvalue()), 0)

if __name__ == "__main__":
    unittest.main()
