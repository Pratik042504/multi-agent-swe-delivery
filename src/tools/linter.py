"""Pre-QA Fast Linter utilizing Ruff with graceful fallback to py_compile and AST validation."""

import ast
import json
import os
import py_compile
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Any

@dataclass
class LinterResult:
    """Result of fast pre-QA linting pass."""
    passed: bool
    errors: List[str]
    diagnostics: List[Dict[str, Any]]
    tool_used: str  # "ruff" or "py_compile"
    execution_time_ms: float

def run_fast_linter(workspace_path: Path, files: Optional[List[str]] = None) -> LinterResult:
    """Runs fast linting on Python files in workspace before QA execution.
    
    Tries 'ruff check --output-format=json' first. If ruff is not installed or
    fails, falls back immediately to py_compile and AST validation.
    
    Args:
        workspace_path: Path to the workspace containing source files.
        files: Optional relative file paths to check. If None, checks all .py files.
        
    Returns:
        LinterResult with pass/fail status, detailed diagnostics, and execution duration.
    """
    start_time = time.perf_counter()
    workspace_path = Path(workspace_path)

    # Determine files to check
    target_paths: List[Path] = []
    if files:
        for rel in files:
            p = workspace_path / rel
            if p.exists() and p.suffix == ".py":
                target_paths.append(p)
    else:
        for root, dirs, f_list in os.walk(workspace_path):
            if any(part in root for part in [".git", "__pycache__", ".venv", "venv", "node_modules"]):
                continue
            for f in f_list:
                if f.endswith(".py"):
                    target_paths.append(Path(root) / f)

    if not target_paths:
        elapsed = (time.perf_counter() - start_time) * 1000.0
        return LinterResult(
            passed=True,
            errors=[],
            diagnostics=[],
            tool_used="noop",
            execution_time_ms=round(elapsed, 2)
        )

    # 1. Attempt running Ruff if installed
    ruff_bin = shutil.which("ruff")
    if ruff_bin:
        try:
            cmd = [ruff_bin, "check", "--output-format=json"] + [str(p) for p in target_paths]
            proc = subprocess.run(
                cmd,
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                timeout=10
            )
            elapsed = (time.perf_counter() - start_time) * 1000.0
            
            if proc.returncode == 0:
                return LinterResult(
                    passed=True,
                    errors=[],
                    diagnostics=[],
                    tool_used="ruff",
                    execution_time_ms=round(elapsed, 2)
                )

            # Parse Ruff JSON output
            try:
                ruff_data = json.loads(proc.stdout)
                errors = []
                diagnostics = []
                for item in ruff_data:
                    file_name = Path(item.get("filename", "")).name
                    row = item.get("location", {}).get("row", 1)
                    col = item.get("location", {}).get("column", 1)
                    code = item.get("code", "E999")
                    msg = item.get("message", "Lint violation")
                    err_str = f"{file_name}:{row}:{col}: [{code}] {msg}"
                    errors.append(err_str)
                    diagnostics.append({
                        "file": file_name,
                        "line": row,
                        "column": col,
                        "code": code,
                        "message": msg
                    })

                return LinterResult(
                    passed=len(errors) == 0,
                    errors=errors,
                    diagnostics=diagnostics,
                    tool_used="ruff",
                    execution_time_ms=round(elapsed, 2)
                )
            except Exception:
                pass  # Fall through to py_compile fallback
        except Exception:
            pass  # Fall through to py_compile fallback

    # 2. Fast Fallback: py_compile & AST Validation (< 5ms)
    errors: List[str] = []
    diagnostics: List[Dict[str, Any]] = []

    for target in target_paths:
        # Step A: py_compile
        try:
            py_compile.compile(str(target), doraise=True)
        except py_compile.PyCompileError as e:
            msg = str(e).strip()
            errors.append(f"{target.name}: {msg}")
            diagnostics.append({
                "file": target.name,
                "line": getattr(e, "lineno", 1),
                "column": 0,
                "code": "SYNTAX_ERROR",
                "message": msg
            })
            continue

        # Step B: ast.parse
        try:
            content = target.read_text(encoding="utf-8")
            ast.parse(content, filename=str(target))
        except SyntaxError as e:
            err_line = f"{target.name}:{e.lineno}:{e.offset}: [E999] SyntaxError: {e.msg}"
            errors.append(err_line)
            diagnostics.append({
                "file": target.name,
                "line": e.lineno or 1,
                "column": e.offset or 0,
                "code": "E999",
                "message": f"SyntaxError: {e.msg}"
            })

    elapsed = (time.perf_counter() - start_time) * 1000.0
    passed = (len(errors) == 0)

    return LinterResult(
        passed=passed,
        errors=errors,
        diagnostics=diagnostics,
        tool_used="py_compile",
        execution_time_ms=round(elapsed, 2)
    )

def lint_in_memory_files(files_dict: Dict[str, str]) -> LinterResult:
    """Pre-validates in-memory Python files using AST before writing or executing."""
    start_time = time.perf_counter()
    errors = []
    diagnostics = []

    for filename, code in files_dict.items():
        if not filename.endswith(".py"):
            continue
        try:
            ast.parse(code, filename=filename)
        except SyntaxError as e:
            err_str = f"{filename}:{e.lineno}:{e.offset}: [E999] SyntaxError: {e.msg}"
            errors.append(err_str)
            diagnostics.append({
                "file": filename,
                "line": e.lineno or 1,
                "column": e.offset or 0,
                "code": "E999",
                "message": f"SyntaxError: {e.msg}"
            })

    elapsed = (time.perf_counter() - start_time) * 1000.0
    return LinterResult(
        passed=len(errors) == 0,
        errors=errors,
        diagnostics=diagnostics,
        tool_used="ast_in_memory",
        execution_time_ms=round(elapsed, 2)
    )

class FastLinter:
    """Convenience class wrapper around run_fast_linter."""
    def lint_files(self, files: List[Path]) -> LinterResult:
        if not files:
            return LinterResult(passed=True, errors=[], diagnostics=[], tool_used="noop", execution_time_ms=0.0)
        parent = files[0].parent
        rel_files = [f.name for f in files]
        return run_fast_linter(parent, rel_files)
