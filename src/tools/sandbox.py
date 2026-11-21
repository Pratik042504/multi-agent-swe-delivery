import os
import subprocess
import sys
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from ..config import get_sandbox_docker_image

@dataclass
class SandboxResult:
    passed: bool
    summary: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    stdout: str
    stderr: str
    error_trace: List[str]

class ExecutionSandbox:
    """Executes code and unit tests in an isolated workspace."""
    
    def __init__(
        self,
        workspace_path: Path,
        use_docker: bool = False,
        docker_image: Optional[str] = None
    ):
        self.workspace_path = workspace_path
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self.use_docker = use_docker
        self.docker_image = docker_image or get_sandbox_docker_image()

    def write_files(self, files: Dict[str, str]) -> None:
        """Write all source and test files to the workspace."""
        for relative_path, content in files.items():
            file_path = self.workspace_path / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

    def run_tests(self, timeout_seconds: int = 45) -> SandboxResult:
        """Run pytest inside the sandbox workspace."""
        if self.use_docker:
            return self._run_docker_tests(timeout_seconds)
        return self._run_local_tests(timeout_seconds)

    def _run_local_tests(self, timeout: int) -> SandboxResult:
        # Check if pytest is available, otherwise fallback to unittest
        cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short"]
        
        env = os.environ.copy()
        # Add workspace root to PYTHONPATH so imports like `from src.foo import bar` or `import foo` resolve
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{str(self.workspace_path.resolve())}{os.pathsep}{existing_pythonpath}"

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.workspace_path),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env
            )
            
            stdout = result.stdout
            stderr = result.stderr

            # If pytest is not installed, fallback to standard unittest runner
            if "No module named pytest" in stderr or "No module named pytest" in stdout:
                unittest_cmd = [sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"]
                result = subprocess.run(
                    unittest_cmd,
                    cwd=str(self.workspace_path),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env
                )
                stdout = result.stdout
                stderr = result.stderr

            passed = (result.returncode == 0)
            
            # Parse test counts from output
            passed_count, failed_count, total_count = self._parse_pytest_output(stdout + "\n" + stderr)
            errors = self._extract_errors(stdout + "\n" + stderr)

            summary = (
                f"Tests Passed: {passed_count}/{total_count}" 
                if passed else 
                f"Tests Failed: {failed_count} failed out of {total_count}"
            )

            return SandboxResult(
                passed=passed,
                summary=summary,
                total_tests=total_count,
                passed_tests=passed_count,
                failed_tests=failed_count,
                stdout=stdout,
                stderr=stderr,
                error_trace=errors
            )

        except subprocess.TimeoutExpired:
            return SandboxResult(
                passed=False,
                summary=f"Execution timed out after {timeout} seconds",
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                stdout="",
                stderr="TimeoutExpired",
                error_trace=["Process killed due to execution timeout."]
            )
        except Exception as e:
            return SandboxResult(
                passed=False,
                summary=f"Execution failed to run: {str(e)}",
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                stdout="",
                stderr=str(e),
                error_trace=[str(e)]
            )

    def _run_docker_tests(self, timeout: int) -> SandboxResult:
        """Runs the test suite inside an isolated Docker container."""
        try:
            import docker
            client = docker.from_env()
            
            # Mount workspace into /workspace in python container
            container_workspace = "/workspace"
            volumes = {
                str(self.workspace_path.resolve()): {
                    "bind": container_workspace,
                    "mode": "rw"
                }
            }
            
            container = client.containers.run(
                image=self.docker_image,
                command="sh -c 'pytest -v --tb=short || python -m unittest discover -s . -p \"test_*.py\"'",
                volumes=volumes,
                working_dir=container_workspace,
                user="sandbox",
                detach=True,
                environment={"PYTHONPATH": container_workspace}
            )
            
            response = container.wait(timeout=timeout)
            exit_code = response.get("StatusCode", 1)
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            container.remove(force=True)

            passed_count, failed_count, total = self._parse_pytest_output(stdout)
            errors = self._extract_errors(stdout + "\n" + stderr)

            return SandboxResult(
                passed=(exit_code == 0),
                summary="Docker tests succeeded" if exit_code == 0 else "Docker tests failed",
                total_tests=total,
                passed_tests=passed_count,
                failed_tests=failed_count,
                stdout=stdout,
                stderr=stderr,
                error_trace=errors
            )
        except Exception as e:
            # Fall back to local if Docker daemon is not active
            return self._run_local_tests(timeout)

    @staticmethod
    def _parse_pytest_output(output: str) -> tuple[int, int, int]:
        passed = 0
        failed = 0
        
        # Pytest patterns: "3 passed, 1 failed"
        passed_match = re.search(r"(\d+)\s+passed", output)
        if passed_match:
            passed = int(passed_match.group(1))

        failed_match = re.search(r"(\d+)\s+failed", output)
        if failed_match:
            failed = int(failed_match.group(1))

        error_match = re.search(r"(\d+)\s+error", output)
        if error_match:
            failed += int(error_match.group(1))

        # Unittest fallback patterns: "Ran 4 tests" ... "OK" or "FAILED (failures=1, errors=1)"
        if passed == 0 and failed == 0:
            ran_match = re.search(r"Ran\s+(\d+)\s+tests?", output)
            if ran_match:
                total_ran = int(ran_match.group(1))
                if "OK" in output and "FAILED" not in output:
                    passed = total_ran
                else:
                    fail_cnt = re.search(r"failures=(\d+)", output)
                    err_cnt = re.search(r"errors=(\d+)", output)
                    f_val = int(fail_cnt.group(1)) if fail_cnt else 0
                    e_val = int(err_cnt.group(1)) if err_cnt else 0
                    failed = f_val + e_val
                    passed = max(0, total_ran - failed)

        total = passed + failed
        return passed, failed, total

        total = passed + failed
        return passed, failed, total

    @staticmethod
    def _extract_errors(full_output: str) -> List[str]:
        errors = []
        # Extract lines starting with FAILED or ERROR or Traceback
        for line in full_output.splitlines():
            line_str = line.strip()
            if line_str.startswith("FAILED ") or line_str.startswith("ERROR ") or "AssertionError:" in line_str:
                errors.append(line_str)
        return errors[:10]
