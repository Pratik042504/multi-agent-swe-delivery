"""Unit and integration tests for the Rich Live Dashboard, HITL gates, and trace replay."""

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.ui.compat import strip_markup, is_interactive, Table, Panel, Layout
from src.ui.dashboard import LiveDashboard
from src.ui.hitl import prompt_hitl_gate
from src.ui.runs_viewer import load_all_runs, display_runs_table
from src.ui.trace_replayer import replay_trace
from src.agents.base import TelemetryTracker
from src.config import get_trace_log_dir

class TestRichTUIDashboard(unittest.TestCase):
    def test_strip_markup(self):
        styled = "[bold green]DELIVERED[/bold green] [dim](v1.0)[/dim]"
        cleaned = strip_markup(styled)
        self.assertEqual(cleaned, "DELIVERED (v1.0)")

    def test_live_dashboard_pipeline_transitions(self):
        """Verify LiveDashboard initializes panels and updates status badges and latency."""
        dash = LiveDashboard(
            thread_id="test_ui_thread_1",
            user_request="Build high-throughput queue",
            session_id="test_ui_thread_1",
            max_budget_usd=10.0
        )
        dash.interactive = False  # Test in non-TTY safe mode

        # 1. Product Node Start & Finish
        dash.on_node_event("product_spec", "start", {"status": "in_progress"})
        self.assertEqual(dash.node_states["product_spec"]["status"], "RUNNING")

        state_sample = {
            "prd": {"title": "Queue Spec", "summary": "FIFO queue"},
            "iteration_count": 0
        }
        dash.on_node_event("product_spec", "finish", state_sample)
        self.assertEqual(dash.node_states["product_spec"]["status"], "PASSED")
        self.assertGreaterEqual(dash.node_states["product_spec"]["latency_ms"], 0.0)

        # 2. Coder Node generates diff
        coder_state = {
            "source_files": {"queue.py": "class Queue:\n    pass\n"},
            "iteration_count": 0
        }
        dash.on_node_event("coder", "finish", coder_state)
        self.assertIsNotNone(dash.latest_diff)
        self.assertIn("queue.py", dash.latest_diff)

        # 3. QA Node retry badge
        dash.on_node_event("qa_test", "finish", {
            "test_results": {"passed": False, "passed_tests": 1, "total_tests": 3},
            "iteration_count": 1,
            "max_iterations": 3
        })
        self.assertEqual(dash.node_states["qa_test"]["status"], "RETRY 1/3")

        # 4. Security Node blocked badge
        dash.on_node_event("security_review", "finish", {
            "security_review": {"approved": False, "quality_score": 4.5, "findings": [{"issue": "eval used"}]}
        })
        self.assertEqual(dash.node_states["security_review"]["status"], "BLOCKED")

        # 5. HITL Interruption badge
        dash.on_node_event("architect", "interrupt", {"status": "interrupted_before_architect"})
        self.assertEqual(dash.node_states["architect"]["status"], "PAUSED (HITL)")

    def test_hitl_gate_non_interactive_fallback(self):
        """In non-TTY environments, HITL gate approves if auto_approve is set, otherwise quits."""
        with patch("src.ui.hitl.is_interactive", return_value=False):
            # Auto-approved state
            action, _ = prompt_hitl_gate("architect", {"human_approved": True, "prd": {}})
            self.assertEqual(action, "approve")

            # Non auto-approved state
            action_quit, _ = prompt_hitl_gate("devops", {"human_approved": False})
            self.assertEqual(action_quit, "quit")

    def test_hitl_gate_interactive_inputs(self):
        """Verify user actions [A]pprove, [E]dit feedback, [Q]uit in interactive prompt."""
        with patch("src.ui.hitl.is_interactive", return_value=True):
            # Test Approve
            with patch("src.ui.hitl.Prompt.ask", return_value="A"):
                action, feedback = prompt_hitl_gate("architect", {"prd": {"title": "Test"}})
                self.assertEqual(action, "approve")
                self.assertEqual(feedback, "")

            # Test Edit Feedback
            with patch("src.ui.hitl.Prompt.ask", side_effect=["E", "Add rate-limiting and redis backend"]):
                action, feedback = prompt_hitl_gate("devops", {"test_results": {"passed": True}})
                self.assertEqual(action, "edit")
                self.assertIn("redis", feedback)

            # Test Quit
            with patch("src.ui.hitl.Prompt.ask", return_value="Q"):
                action, _ = prompt_hitl_gate("devops", {})
                self.assertEqual(action, "quit")

    def test_runs_viewer_aggregation_and_display(self):
        """Verify load_all_runs loads checkpoints and trace logs into structured table entries."""
        runs = load_all_runs()
        self.assertIsInstance(runs, list)
        # Should not raise exception
        display_runs_table(runs)

    def test_trace_replay_execution(self):
        """Verify trace replay reads JSONL events and formats steps."""
        run_id = "test_replay_run_42"
        tracker = TelemetryTracker.get_tracker(run_id)
        tracker.record_call(
            agent_name="CoderAgent",
            event_type="code_generation",
            prompt_tokens=150,
            completion_tokens=220,
            latency_ms=120.0,
            cost_usd=0.0012,
            metadata={"summary": "Generated microservice architecture", "diff": "--- a/api.py\n+++ b/api.py\n+def health(): return 200"}
        )
        tracker.record_transition(
            from_node="coder",
            to_node="qa_test",
            diff="--- a/api.py\n+++ b/api.py\n+def health(): return 200",
            metadata={"step": 2, "status": "code_generated"}
        )

        # Replay trace in non-interactive mode
        success = replay_trace(run_id=run_id, interactive=False)
        self.assertTrue(success)

        # Verify missing trace returns False
        missing_success = replay_trace(run_id="non_existent_run_9999", interactive=False)
        self.assertFalse(missing_success)

if __name__ == "__main__":
    unittest.main()
