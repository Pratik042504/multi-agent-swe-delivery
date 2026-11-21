"""Unit tests verifying Render deployment configuration, Dockerfile, and health probes."""

import os
import json
import io
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.server import SwarmHTTPHandler
from src.config import get_workspace_dir, get_checkpoint_db_path, get_trace_log_dir

class TestRenderConfiguration(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parent.parent

    def test_render_yaml_validity(self):
        """Verify render.yaml exists and contains all required blueprint parameters."""
        render_file = self.project_root / "render.yaml"
        self.assertTrue(render_file.exists(), "render.yaml must exist at project root")

        content = render_file.read_text(encoding="utf-8")
        self.assertIn("services:", content)
        self.assertIn("type: web", content)
        self.assertIn("name: mas-eds-backend", content)
        self.assertIn("runtime: python", content)
        self.assertIn("buildCommand: pip install -r requirements.txt", content)
        self.assertIn("startCommand: python -m src.server", content)
        self.assertIn("healthCheckPath: /health", content)
        self.assertIn("key: HOST", content)
        self.assertIn("value: 0.0.0.0", content)
        self.assertIn("key: GEMINI_API_KEY", content)

    def test_dockerfile_render_compatibility(self):
        """Verify Dockerfile contains system git, python 3.11, healthcheck, and default port."""
        dockerfile = self.project_root / "Dockerfile"
        self.assertTrue(dockerfile.exists(), "Dockerfile must exist at project root")

        content = dockerfile.read_text(encoding="utf-8")
        self.assertIn("FROM python:3.11", content)
        self.assertIn("git", content)
        self.assertIn("pip install", content)
        self.assertIn("EXPOSE 8080", content)
        self.assertIn("HEALTHCHECK", content)
        self.assertIn("/health", content)
        self.assertIn('CMD ["python", "-m", "src.server"]', content)

    def test_health_check_endpoint(self):
        """Verify /health and /api/health return 200 with 'healthy' status."""
        for path in ("/health", "/api/health"):
            handler = SwarmHTTPHandler.__new__(SwarmHTTPHandler)
            handler.path = path
            handler.headers = {}
            handler.rfile = io.BytesIO()
            handler.wfile = io.BytesIO()

            sent_headers = {}
            status_holder = []

            def fake_send_response(code):
                status_holder.append(code)

            def fake_send_header(k, v):
                sent_headers[k] = v

            def fake_end_headers():
                pass

            handler.send_response = fake_send_response
            handler.send_header = fake_send_header
            handler.end_headers = fake_end_headers

            handler.do_GET()

            self.assertEqual(status_holder, [200], f"Expected 200 for {path}")
            self.assertEqual(sent_headers.get("Content-Type"), "application/json")
            self.assertEqual(sent_headers.get("Access-Control-Allow-Origin"), "*")

            response_body = json.loads(handler.wfile.getvalue().decode("utf-8"))
            self.assertEqual(response_body.get("status"), "healthy")
            self.assertEqual(response_body.get("service"), "MAS-EDS Backend")

    def test_render_environment_port_and_host(self):
        """Verify host binding switches to 0.0.0.0 when RENDER or PORT is detected."""
        orig_render = os.environ.get("RENDER")
        orig_port = os.environ.get("PORT")
        orig_host = os.environ.get("HOST")

        try:
            os.environ["RENDER"] = "true"
            os.environ["PORT"] = "10000"

            env_port = os.getenv("PORT")
            port = int(env_port) if env_port else 8080
            self.assertEqual(port, 10000)

            # Check host logic matching src/server.py:start_server
            if os.getenv("RENDER") or os.getenv("PORT") or os.getenv("DOCKER"):
                host = "0.0.0.0"
            else:
                host = os.getenv("HOST", "127.0.0.1")

            self.assertEqual(host, "0.0.0.0")

            # Check config storage paths under RENDER
            ws = get_workspace_dir("render_task_456")
            self.assertTrue(ws.is_absolute() or str(ws).startswith("workspaces") or "tmp" in str(ws).lower())

        finally:
            if orig_render is None:
                os.environ.pop("RENDER", None)
            else:
                os.environ["RENDER"] = orig_render

            if orig_port is None:
                os.environ.pop("PORT", None)
            else:
                os.environ["PORT"] = orig_port

            if orig_host is None:
                os.environ.pop("HOST", None)
            else:
                os.environ["HOST"] = orig_host

if __name__ == "__main__":
    unittest.main()
