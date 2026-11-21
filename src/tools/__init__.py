"""Tools for Sandboxed Execution, Git Control, CI/CD Delivery, Diff Editing, Linting, and Dependency Management."""

from .sandbox import ExecutionSandbox, SandboxResult
from .git_tools import GitManager
from .cicd_generator import generate_dockerfile, generate_github_workflow
from .diff_editor import apply_search_replace, apply_unified_diff, DiffResult
from .linter import run_fast_linter, lint_in_memory_files, LinterResult
from .dependency_manager import (
    extract_missing_dependencies,
    update_requirements_txt,
    resolve_and_update_dependencies,
    DependencyResult
)

__all__ = [
    "ExecutionSandbox",
    "SandboxResult",
    "GitManager",
    "generate_dockerfile",
    "generate_github_workflow",
    "apply_search_replace",
    "apply_unified_diff",
    "DiffResult",
    "run_fast_linter",
    "lint_in_memory_files",
    "LinterResult",
    "extract_missing_dependencies",
    "update_requirements_txt",
    "resolve_and_update_dependencies",
    "DependencyResult"
]
