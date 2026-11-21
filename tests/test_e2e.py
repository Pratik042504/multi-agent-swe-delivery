"""End-to-End Self-Healing Pipeline Integration Test.

Verifies complete multi-agent SDLC loop:
1. Product & Architecture specification generation.
2. Initial code deployment with intentional bug.
3. Fast linter check & Sandbox QA failure detection with error compaction.
4. Targeted surgical diff patch application via diff_editor.py.
5. Sandbox QA re-verification & Security AST/Bandit approval.
6. DevOps automated container packaging, Git feature branch commit,
   SQLite checkpoint persistence, and JSONL trace recording.
"""

import unittest
import tempfile
import gc
from pathlib import Path

from src.agents import (
    ProductAgent,
    ArchitectAgent,
    QAAgent,
    SecurityAgent,
    DevOpsAgent
)
from src.agents.base import TelemetryTracker, active_run_id
from src.tools.linter import FastLinter
from src.tools.diff_editor import apply_search_replace, validate_python_syntax
from src.tools.git_tools import GitManager
from src.state import reduce_error_logs
from src.graph import SqliteCheckpointer
from src.config import get_trace_log_dir

class TestSelfHealingDeliveryPipelineE2E(unittest.TestCase):
    def test_e2e_self_healing_sdlc_lifecycle(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace_path = Path(tmp_dir) / "workspace"
            workspace_path.mkdir(parents=True, exist_ok=True)
            db_path = Path(tmp_dir) / "checkpoints.db"

            run_id = "e2e_self_healing_run_001"
            thread_id = run_id
            active_run_id.set(run_id)
            tracker = TelemetryTracker.get_tracker(run_id)

            # Initialize Git repository in workspace
            git_mgr = GitManager(workspace_path)
            (workspace_path / "README.md").write_text("# Rate Limiter Service\n", encoding="utf-8")
            git_mgr.commit_changes("Initial commit")

            # ---------------------------------------------------------
            # Step 1: Product & Architecture Specification
            # ---------------------------------------------------------
            user_prompt = "Build an in-memory token bucket rate limiter service"
            product_agent = ProductAgent()
            prd = product_agent.run(user_prompt)
            self.assertIn("title", prd)
            self.assertIn("features", prd)
            self.assertGreater(len(prd["features"]), 0)

            architect_agent = ArchitectAgent()
            arch = architect_agent.run(user_prompt, prd)
            self.assertIn("system_overview", arch)
            self.assertIn("source_files_planned", arch)
            self.assertIn("execution_entrypoint", arch)

            # ---------------------------------------------------------
            # Step 2: Write Initial Code with Intentional Bug
            # ---------------------------------------------------------
            buggy_source = (
                '"""Token bucket rate limiter."""\n'
                'class TokenBucketLimiter:\n'
                '    def __init__(self, capacity: int):\n'
                '        self.capacity = capacity\n'
                '        self.tokens = 0  # INTENTIONAL BUG: should initialize with capacity\n'
                '\n'
                '    def allow_request(self) -> bool:\n'
                '        if self.tokens > 0:\n'
                '            self.tokens -= 1\n'
                '            return True\n'
                '        return False\n'
            )

            test_suite = (
                'import unittest\n'
                'from limiter import TokenBucketLimiter\n'
                '\n'
                'class TestTokenBucketLimiter(unittest.TestCase):\n'
                '    def test_initial_tokens_allowed(self):\n'
                '        limiter = TokenBucketLimiter(capacity=5)\n'
                '        self.assertTrue(limiter.allow_request(), "New limiter must allow immediate initial request")\n'
                '\n'
                'if __name__ == "__main__":\n'
                '    unittest.main()\n'
            )

            source_file = workspace_path / "limiter.py"
            test_file = workspace_path / "test_limiter.py"
            source_file.write_text(buggy_source, encoding="utf-8")
            test_file.write_text(test_suite, encoding="utf-8")

            # ---------------------------------------------------------
            # Step 3: Attempt 1 - Linter & QA Failure Detection
            # ---------------------------------------------------------
            linter = FastLinter()
            lint_res = linter.lint_files([source_file, test_file])
            self.assertTrue(lint_res.passed, "Valid Python syntax should pass linter check")

            qa_agent = QAAgent()
            qa_res_1 = qa_agent.run(
                workspace_dir=workspace_path,
                source_files={"limiter.py": buggy_source},
                test_files={"test_limiter.py": test_suite},
                use_docker=False
            )
            self.assertFalse(qa_res_1["passed"], "Attempt 1 must fail due to intentional bug")
            self.assertGreaterEqual(qa_res_1["failed_tests"], 1)

            # Compact error logs into state
            compacted_errors = reduce_error_logs([], qa_res_1["errors"])
            self.assertGreater(len(compacted_errors), 0)

            # ---------------------------------------------------------
            # Step 4: Attempt 2 - Surgical Diff Patch via diff_editor.py
            # ---------------------------------------------------------
            patch_target = "self.tokens = 0  # INTENTIONAL BUG: should initialize with capacity"
            patch_replacement = "self.tokens = self.capacity"
            
            diff_res = apply_search_replace(source_file, [(patch_target, patch_replacement)])
            self.assertTrue(diff_res.success, f"Surgical diff patch failed: {diff_res.error}")
            self.assertIn("limiter.py", diff_res.diff)

            # Validate syntax of patched file
            patched_content = source_file.read_text(encoding="utf-8")
            syntax_err = validate_python_syntax(patched_content, "limiter.py")
            self.assertIsNone(syntax_err, "Patched file must have valid Python syntax")

            # ---------------------------------------------------------
            # Step 5: QA Re-verification & Security AST Scan
            # ---------------------------------------------------------
            qa_res_2 = qa_agent.run(
                workspace_dir=workspace_path,
                source_files={"limiter.py": patched_content},
                test_files={"test_limiter.py": test_suite},
                use_docker=False
            )
            self.assertTrue(qa_res_2["passed"], f"Attempt 2 must pass after surgical patch: {qa_res_2['errors']}")
            self.assertGreaterEqual(qa_res_2["passed_tests"], 1)
            self.assertEqual(qa_res_2["failed_tests"], 0)

            # Security AST & Bandit Audit
            sec_agent = SecurityAgent()
            sec_review = sec_agent.run(
                {"limiter.py": patched_content},
                workspace_path=workspace_path
            )
            self.assertTrue(sec_review["approved"], "Patched safe code must pass security review")
            self.assertGreaterEqual(sec_review["quality_score"], 8.0)

            # ---------------------------------------------------------
            # Step 6: DevOps Delivery Package & Git Commit
            # ---------------------------------------------------------
            devops_agent = DevOpsAgent()
            app_name = "token-limiter"
            delivery_artifacts = devops_agent.run(
                workspace_dir=workspace_path,
                app_name=app_name,
                entrypoint="limiter.py",
                prd_summary=prd["summary"],
                test_summary=qa_res_2["summary"],
                security_summary=sec_review["summary"]
            )
            self.assertIn("dockerfile", delivery_artifacts)
            self.assertIn("github_workflow", delivery_artifacts)
            self.assertTrue((workspace_path / "Dockerfile").exists())
            self.assertTrue((workspace_path / ".github" / "workflows" / "ci-cd.yml").exists())

            # Verify Git branch and commit created
            active_branch = git_mgr.get_current_branch()
            self.assertIn(app_name, active_branch)
            commit_log = git_mgr.get_commit_log(n=3)
            self.assertTrue(any("automated-delivery" in c.lower() or app_name in c.lower() for c in commit_log))

            # ---------------------------------------------------------
            # Step 7: SQLite Checkpoint & JSONL Telemetry Verification
            # ---------------------------------------------------------
            checkpointer = SqliteCheckpointer(db_path=db_path)
            state_payload = {
                "session_id": thread_id,
                "thread_id": thread_id,
                "status": "delivered",
                "human_approved": True,
                "iteration_count": 1
            }
            checkpointer.put({"configurable": {"thread_id": thread_id}}, state_payload)
            loaded_checkpoint = checkpointer.get({"configurable": {"thread_id": thread_id}})
            self.assertIsNotNone(loaded_checkpoint)
            self.assertEqual(loaded_checkpoint.get("status"), "delivered")
            self.assertEqual(loaded_checkpoint.get("iteration_count"), 1)

            # Record telemetry event and verify JSONL trace file
            tracker.record_transition(
                from_node="coder",
                to_node="qa_test",
                diff=diff_res.diff,
                metadata={"iteration": 1, "status": "healed"}
            )
            trace_file = get_trace_log_dir() / f"{run_id}.jsonl"
            self.assertTrue(trace_file.exists(), f"Trace log {trace_file} must exist")
            trace_lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertGreater(len(trace_lines), 0)

            del checkpointer
            gc.collect()

if __name__ == "__main__":
    unittest.main()
