from pathlib import Path
from typing import Dict, Any
from .base import BaseAgent
from ..state import TestExecutionResult
from ..tools.sandbox import ExecutionSandbox
from ..tools.linter import run_fast_linter
from ..tools.dependency_manager import resolve_and_update_dependencies

class QAAgent(BaseAgent):
    """QA & Verification Agent: Executes fast linting and unit tests in isolated sandbox."""

    def __init__(self):
        super().__init__(name="QAVerifier", role="Staff Quality Assurance Engineer")

    def run(
        self,
        workspace_dir: Path,
        source_files: Dict[str, str],
        test_files: Dict[str, str],
        use_docker: bool = False
    ) -> Dict[str, Any]:
        
        sandbox = ExecutionSandbox(workspace_dir, use_docker=use_docker)
        
        # 1. Deploy all files to sandbox
        all_files = {**source_files, **test_files}
        sandbox.write_files(all_files)

        # 2. Fast Pre-QA Linter pass (< 5ms)
        linter_res = run_fast_linter(workspace_dir, files=list(all_files.keys()))
        if not linter_res.passed:
            # Fast fail immediately before spinning up test runner
            return TestExecutionResult(
                passed=False,
                summary=f"Pre-QA Linter Failed [{linter_res.tool_used} in {linter_res.execution_time_ms}ms]",
                total_tests=len(linter_res.errors),
                passed_tests=0,
                failed_tests=len(linter_res.errors),
                stdout="",
                stderr="\n".join(linter_res.errors),
                errors=linter_res.errors
            ).model_dump()

        # 3. Run unit tests inside the sandbox
        result = sandbox.run_tests(timeout_seconds=30)

        # 4. If failed with missing imports, trigger dynamic dependency manager
        if not result.passed:
            dep_res = resolve_and_update_dependencies(
                workspace_dir,
                result.stdout + "\n" + result.stderr,
                auto_install=True
            )
            if dep_res.missing_packages:
                result.error_trace.append(dep_res.summary)

        qa_result = TestExecutionResult(
            passed=result.passed,
            summary=result.summary,
            total_tests=result.total_tests,
            passed_tests=result.passed_tests,
            failed_tests=result.failed_tests,
            stdout=result.stdout,
            stderr=result.stderr,
            errors=result.error_trace
        )
        return qa_result.model_dump()
