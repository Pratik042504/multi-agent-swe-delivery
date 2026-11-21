"""Unit tests verifying Vercel deployment configuration and serverless handler."""

import os
import json
import unittest
from pathlib import Path
from src.server import SwarmHTTPHandler
from src.config import get_workspace_dir, get_checkpoint_db_path, get_trace_log_dir

class TestVercelConfiguration(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parent.parent

    def test_vercel_json_validity(self):
        """Verify vercel.json exists and contains correct routes and builds."""
        vercel_file = self.project_root / "vercel.json"
        self.assertTrue(vercel_file.exists(), "vercel.json must exist at project root")

        with open(vercel_file, "r", encoding="utf-8") as f:
            config = json.load(f)

        self.assertEqual(config.get("version"), 2)
        builds = config.get("builds", [])
        self.assertTrue(any(b.get("src") == "api/index.py" and b.get("use") == "@vercel/python" for b in builds))
        
        routes = config.get("routes", [])
        self.assertTrue(any(r.get("dest") == "/api/index.py" for r in routes))

    def test_vercelignore_exists(self):
        """Verify .vercelignore excludes local cache and workspace directories."""
        vignore = self.project_root / ".vercelignore"
        self.assertTrue(vignore.exists(), ".vercelignore must exist")
        content = vignore.read_text(encoding="utf-8")
        self.assertIn(".venv", content)
        self.assertIn("workspaces", content)
        self.assertIn(".swe_cache", content)

    def test_api_index_handler_import(self):
        """Verify api/index.py imports cleanly and subclasses SwarmHTTPHandler."""
        from api.index import handler
        self.assertTrue(issubclass(handler, SwarmHTTPHandler))

    def test_vercel_tmp_storage_resolution(self):
        """Verify that when VERCEL=1 is set, storage paths default to writable /tmp."""
        orig_vercel = os.environ.get("VERCEL")
        try:
            os.environ["VERCEL"] = "1"
            # In Vercel mode, paths must reside inside temporary directory
            ws = get_workspace_dir("vercel_task_123")
            self.assertTrue("tmp" in str(ws).lower() or "temp" in str(ws).lower())
        finally:
            if orig_vercel is None:
                os.environ.pop("VERCEL", None)
            else:
                os.environ["VERCEL"] = orig_vercel

if __name__ == "__main__":
    unittest.main()
