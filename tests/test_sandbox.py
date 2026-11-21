import unittest
import tempfile
from pathlib import Path
from src.tools.sandbox import ExecutionSandbox

class TestExecutionSandbox(unittest.TestCase):
    def test_sandbox_local_execution_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sandbox = ExecutionSandbox(Path(tmp_dir), use_docker=False)
            
            files = {
                "calculator.py": "def add(a, b):\n    return a + b\n",
                "test_calculator.py": (
                    "import unittest\n"
                    "from calculator import add\n\n"
                    "class TestCalc(unittest.TestCase):\n"
                    "    def test_add(self):\n"
                    "        self.assertEqual(add(2, 3), 5)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            }
            sandbox.write_files(files)
            
            result = sandbox.run_tests(timeout_seconds=15)
            self.assertTrue(result.passed, f"Tests failed: {result.stdout} {result.stderr}")
            self.assertGreaterEqual(result.total_tests, 1)
            self.assertGreaterEqual(result.passed_tests, 1)
            self.assertEqual(result.failed_tests, 0)

    def test_sandbox_local_execution_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sandbox = ExecutionSandbox(Path(tmp_dir), use_docker=False)
            
            files = {
                "calculator.py": "def add(a, b):\n    return a - b\n",  # intentional bug
                "test_calculator.py": (
                    "import unittest\n"
                    "from calculator import add\n\n"
                    "class TestCalc(unittest.TestCase):\n"
                    "    def test_add(self):\n"
                    "        self.assertEqual(add(2, 3), 5)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            }
            sandbox.write_files(files)
            
            result = sandbox.run_tests(timeout_seconds=15)
            self.assertFalse(result.passed)
            self.assertGreaterEqual(result.failed_tests, 1)

    def test_sandbox_docker_image_configuration(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Default docker image from config
            sb_default = ExecutionSandbox(Path(tmp_dir), use_docker=True)
            self.assertEqual(sb_default.docker_image, "swe-sandbox:latest")

            # Custom docker image
            sb_custom = ExecutionSandbox(Path(tmp_dir), use_docker=True, docker_image="custom-sandbox:v1")
            self.assertEqual(sb_custom.docker_image, "custom-sandbox:v1")

if __name__ == "__main__":
    unittest.main()
