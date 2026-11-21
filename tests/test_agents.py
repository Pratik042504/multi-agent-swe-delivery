import unittest
import tempfile
import json
from pathlib import Path
from src.agents.security_agent import SecurityAgent
from src.agents.base import TelemetryTracker, BudgetExceededError, calculate_token_cost
from src.config import get_trace_log_dir

class TestSecurityAgent(unittest.TestCase):
    def test_security_agent_detects_eval(self):
        agent = SecurityAgent()
        vulnerable_code = {
            "bad_service.py": "def run_input(user_input):\n    return eval(user_input)\n"
        }
        review = agent.run(vulnerable_code)
        self.assertFalse(review["approved"])
        self.assertFalse(review["passed"])
        self.assertGreaterEqual(len(review["findings"]), 1)
        self.assertTrue(any("eval" in f["issue"] for f in review["findings"]))

    def test_security_agent_approves_safe_code(self):
        agent = SecurityAgent()
        safe_code = {
            "safe_service.py": "def greet(name: str) -> str:\n    return f'Hello {name}'\n"
        }
        review = agent.run(safe_code)
        self.assertTrue(review["approved"])
        self.assertTrue(review["passed"])
        self.assertEqual(len(review["findings"]), 0)

    def test_security_agent_augmented_with_workspace(self):
        """Verify that passing workspace_path triggers Bandit and pip-audit multi-layer scanning."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            req_file = workspace / "requirements.txt"
            req_file.write_text("pytest>=8.0.0\n", encoding="utf-8")

            code = {
                "safe_service.py": "def add(a, b): return a + b\n"
            }
            (workspace / "safe_service.py").write_text(code["safe_service.py"], encoding="utf-8")

            agent = SecurityAgent()
            report = agent.run(code, workspace_path=workspace)

            self.assertIn("bandit_scan", report)
            self.assertIn("pip_audit_scan", report)
            self.assertIn("findings", report)
            self.assertIn("quality_score", report)
            self.assertTrue(report["passed"])


class TestTelemetryAndBudget(unittest.TestCase):
    """Test suite for token tracking, cost calculation, and budget enforcement."""

    def test_calculate_token_cost(self):
        cost_gemini = calculate_token_cost("gemini", prompt_tokens=1000, completion_tokens=1000)
        self.assertAlmostEqual(cost_gemini, 0.00125 + 0.005, places=5)

        cost_openai = calculate_token_cost("openai", prompt_tokens=2000, completion_tokens=1000)
        self.assertAlmostEqual(cost_openai, (2 * 0.0025) + 0.010, places=5)

    def test_telemetry_event_logging_jsonl(self):
        run_id = "test_telemetry_trace_001"
        TelemetryTracker.reset(run_id)
        tracker = TelemetryTracker(run_id=run_id, max_budget_usd=10.0)

        tracker.record_call(
            agent_name="TestAgent",
            event_type="llm_call",
            prompt_tokens=150,
            completion_tokens=50,
            latency_ms=120.5,
            cost_usd=0.00045,
            metadata={"model": "gemini-1.5-pro"}
        )

        self.assertEqual(tracker.total_prompt_tokens, 150)
        self.assertEqual(tracker.total_completion_tokens, 50)
        self.assertAlmostEqual(tracker.cumulative_cost_usd, 0.00045, places=6)

        # Verify JSONL trace file exists and contains valid JSON
        trace_file = get_trace_log_dir() / f"{run_id}.jsonl"
        self.assertTrue(trace_file.exists())
        lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(lines), 1)

        event = json.loads(lines[-1])
        self.assertEqual(event["run_id"], run_id)
        self.assertEqual(event["agent"], "TestAgent")
        self.assertEqual(event["prompt_tokens"], 150)
        self.assertEqual(event["cost_usd"], 0.00045)

    def test_budget_exceeded_error(self):
        run_id = "test_budget_exceeded_002"
        TelemetryTracker.reset(run_id)
        # Set tiny budget
        tracker = TelemetryTracker(run_id=run_id, max_budget_usd=0.0001)

        # First small call under budget
        tracker.record_call(
            agent_name="AgentA",
            event_type="llm_call",
            prompt_tokens=10,
            completion_tokens=10,
            latency_ms=50.0,
            cost_usd=0.00005
        )

        # Second call that pushes cumulative cost over $0.0001
        with self.assertRaises(BudgetExceededError):
            tracker.record_call(
                agent_name="AgentB",
                event_type="llm_call",
                prompt_tokens=1000,
                completion_tokens=1000,
                latency_ms=200.0,
                cost_usd=0.005
            )

if __name__ == "__main__":
    unittest.main()
