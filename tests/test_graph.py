import unittest
import tempfile
import gc
from pathlib import Path
from src.graph import (
    create_delivery_graph,
    route_after_qa,
    route_after_security,
    SqliteCheckpointer
)
from src.state import AgentState, reduce_error_logs, PRDSpec, ArchitecturePlan, CodePatch, AuditReport

class TestDeliveryGraph(unittest.TestCase):
    def test_graph_compilation(self):
        graph = create_delivery_graph()
        self.assertIsNotNone(graph)

    def test_route_after_qa_pass(self):
        state: AgentState = {
            "test_results": {"passed": True},
            "iteration_count": 0,
            "max_iterations": 3
        }
        next_node = route_after_qa(state)
        self.assertEqual(next_node, "security_review")

    def test_route_after_qa_retry(self):
        state: AgentState = {
            "test_results": {"passed": False},
            "iteration_count": 1,
            "max_iterations": 3
        }
        next_node = route_after_qa(state)
        self.assertEqual(next_node, "coder")
        self.assertEqual(state["iteration_count"], 2)

    def test_route_after_qa_max_exceeded(self):
        state: AgentState = {
            "test_results": {"passed": False},
            "iteration_count": 3,
            "max_iterations": 3
        }
        next_node = route_after_qa(state)
        self.assertEqual(next_node, "__end__")

    def test_route_after_security_pass(self):
        state: AgentState = {
            "security_review": {"approved": True},
            "iteration_count": 0,
            "max_iterations": 3
        }
        next_node = route_after_security(state)
        self.assertEqual(next_node, "devops_delivery")

    def test_route_after_security_retry(self):
        state: AgentState = {
            "security_review": {"approved": False},
            "iteration_count": 1,
            "max_iterations": 3
        }
        next_node = route_after_security(state)
        self.assertEqual(next_node, "coder")
        self.assertEqual(state["iteration_count"], 2)

    def test_reduce_error_logs_compaction(self):
        """Verify that previous attempts are compacted to 1-line summaries while the latest full traceback is preserved."""
        attempt_1 = (
            "Traceback (most recent call last):\n"
            "  File 'service.py', line 42, in create_task\n"
            "ValueError: task_id and title cannot be empty"
        )
        attempt_2 = (
            "Traceback (most recent call last):\n"
            "  File 'service.py', line 99, in complete_task\n"
            "KeyError: 'Task 999 not found'"
        )
        attempt_3 = (
            "Traceback (most recent call last):\n"
            "  File 'test_service.py', line 15, in test_add\n"
            "AssertionError: Expected 5 but got 4"
        )

        compacted = reduce_error_logs([], [attempt_1, attempt_2, attempt_3])
        self.assertEqual(len(compacted), 3)
        self.assertTrue(compacted[0].startswith("[Attempt #1 Failed]:"))
        self.assertIn("ValueError", compacted[0])
        self.assertNotIn("File 'service.py'", compacted[0])  # Stack trace lines compacted away

        self.assertTrue(compacted[1].startswith("[Attempt #2 Failed]:"))
        self.assertIn("KeyError", compacted[1])

        # Latest attempt retains full traceback
        self.assertEqual(compacted[2], attempt_3)
        self.assertIn("Traceback (most recent call last):", compacted[2])

    def test_sqlite_checkpointer_persistence(self):
        """Verify SQLite checkpointer saves and retrieves states across thread_id."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            db_path = Path(tmp_dir) / "checkpoints.db"
            checkpointer = SqliteCheckpointer(db_path=db_path)

            thread_id = "test_thread_001"
            config = {"configurable": {"thread_id": thread_id}}
            saved_state = {
                "session_id": thread_id,
                "status": "spec_ready",
                "iteration_count": 1
            }

            checkpointer.put(config, saved_state)
            loaded = checkpointer.get(config)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.get("session_id"), thread_id)
            self.assertEqual(loaded.get("status"), "spec_ready")
            self.assertEqual(loaded.get("iteration_count"), 1)
            self.assertIn(thread_id, checkpointer.list_checkpoints())

            del checkpointer
            gc.collect()

    def test_graph_interruption_and_resume(self):
        """Verify that interrupt_before pauses the graph and resuming completes it."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            db_path = Path(tmp_dir) / "checkpoints.db"
            checkpointer = SqliteCheckpointer(db_path=db_path)

            thread_id = "resume_test_thread"
            config = {"configurable": {"thread_id": thread_id}}

            # Compile graph configured to interrupt before 'architect'
            graph = create_delivery_graph(
                checkpointer=checkpointer,
                interrupt_before=["architect"],
                interactive_approval=False
            )

            initial_state = {
                "session_id": thread_id,
                "thread_id": thread_id,
                "user_request": "Build a key-value store with TTL",
                "iteration_count": 0,
                "max_iterations": 3,
                "status": "initialized",
                "human_approved": True,
                "audit_log": []
            }

            # 1. First execution: should pause before 'architect'
            paused_state = graph.invoke(initial_state, config=config)
            self.assertEqual(paused_state.get("status"), "interrupted_before_architect")
            self.assertIsNotNone(paused_state.get("prd"))  # Product node completed
            self.assertIsNone(paused_state.get("architecture"))  # Architect was interrupted

            # Verify state was checkpointed to sqlite
            saved = checkpointer.get(config)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.get("status"), "interrupted_before_architect")

            # 2. Resume execution: invoke with empty/continuation state
            resumed_state = graph.invoke({}, config=config)
            self.assertEqual(resumed_state.get("status"), "delivered")
            self.assertIsNotNone(resumed_state.get("architecture"))
            self.assertIsNotNone(resumed_state.get("delivery_artifacts"))

            del checkpointer
            gc.collect()

    def test_pydantic_schemas_instantiation(self):
        """Verify new Pydantic output schemas (PRDSpec, ArchitecturePlan, CodePatch, AuditReport)."""
        prd = PRDSpec(
            title="Test Spec",
            summary="Test summary",
            features=[],
            non_functional_requirements=["perf"]
        )
        self.assertEqual(prd.title, "Test Spec")

        arch = ArchitecturePlan(
            system_overview="Microservice",
            components=["API"],
            dependencies=["fastapi"],
            source_files_planned=["main.py"],
            test_files_planned=["test_main.py"]
        )
        self.assertEqual(arch.components, ["API"])

        patch = CodePatch(
            files={"main.py": "print('hello')"},
            test_files={"test_main.py": "assert True"},
            diffs={"main.py": "+print('hello')"},
            implementation_notes="Clean patch"
        )
        self.assertEqual(len(patch.files), 1)

        report = AuditReport(
            passed=True,
            quality_score=9.8,
            summary="Zero vulnerabilities",
            findings=[],
            bandit_scan={"status": "ok"},
            pip_audit_scan={"status": "ok"}
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.quality_score, 9.8)

if __name__ == "__main__":
    unittest.main()
