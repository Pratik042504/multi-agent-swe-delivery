"""Unit tests for Surgical Diff Editor and AST Repository Map."""

import unittest
import tempfile
from pathlib import Path
from src.tools.diff_editor import (
    apply_search_replace,
    apply_unified_diff,
    validate_python_syntax,
    DiffResult
)
from src.context.repo_map import generate_repo_map, extract_file_ast_map
from src.tools.linter import run_fast_linter, lint_in_memory_files
from src.tools.dependency_manager import (
    extract_missing_dependencies,
    update_requirements_txt,
    validate_package_name
)

class TestDiffEditor(unittest.TestCase):
    """Test suite for Surgical Diff Editor with AST syntax validation."""

    def test_apply_search_replace_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "calculator.py"
            original_code = (
                "def add(a: int, b: int) -> int:\n"
                "    return a + b\n"
            )
            file_path.write_text(original_code, encoding="utf-8")

            search_block = "    return a + b\n"
            replace_block = "    # perform addition\n    return a + b\n"

            res = apply_search_replace(file_path, [(search_block, replace_block)])
            self.assertTrue(res.success, f"Search/replace failed: {res.error}")
            self.assertEqual(res.blocks_applied, 1)
            self.assertEqual(res.blocks_failed, 0)
            self.assertIn("+    # perform addition", res.diff)

            updated_content = file_path.read_text(encoding="utf-8")
            self.assertIn("# perform addition", updated_content)

    def test_apply_search_replace_syntax_error_aborts_write(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "service.py"
            valid_code = (
                "class Service:\n"
                "    def run(self) -> bool:\n"
                "        return True\n"
            )
            file_path.write_text(valid_code, encoding="utf-8")

            # Replacement contains broken, unclosed syntax
            search_block = "        return True\n"
            broken_replace = "        return (\n"

            res = apply_search_replace(file_path, [(search_block, broken_replace)])
            self.assertFalse(res.success)
            self.assertIsNotNone(res.syntax_error_details)
            self.assertIn("SyntaxError", res.error)
            self.assertGreaterEqual(res.syntax_error_details["lineno"], 1)

            # Crucial assertion: File on disk was NOT corrupted or modified!
            disk_content = file_path.read_text(encoding="utf-8")
            self.assertEqual(disk_content, valid_code)

    def test_apply_search_replace_search_not_found(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "test.py"
            file_path.write_text("x = 10\n", encoding="utf-8")

            res = apply_search_replace(file_path, [("nonexistent_pattern", "replacement")])
            self.assertFalse(res.success)
            self.assertIn("not found", res.error.lower())
            self.assertEqual(res.blocks_failed, 1)

    def test_validate_python_syntax(self):
        valid = "def foo(): pass"
        invalid = "def foo( pass"
        self.assertIsNone(validate_python_syntax(valid))
        err = validate_python_syntax(invalid)
        self.assertIsNotNone(err)
        self.assertIn("lineno", err)

    def test_apply_unified_diff_patch(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "mod.py"
            file_path.write_text("x = 1\ny = 2\n", encoding="utf-8")

            patch = (
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -1,2 +1,2 @@\n"
                "-x = 1\n"
                "+x = 100\n"
                " y = 2\n"
            )
            res = apply_unified_diff(file_path, patch)
            self.assertTrue(res.success, f"Patch failed: {res.error}")
            self.assertIn("x = 100", file_path.read_text(encoding="utf-8"))


class TestRepoMap(unittest.TestCase):
    """Test suite for AST Repository Map generator."""

    def test_generate_repo_map_extracts_classes_and_functions(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            
            # Module 1: Models & Classes
            (workspace / "models.py").write_text(
                '"""Domain models."""\n'
                'from typing import Optional, List\n\n'
                'class Task:\n'
                '    """Represents a task."""\n'
                '    def __init__(self, task_id: str, title: str, done: bool = False) -> None:\n'
                '        self.id = task_id\n\n'
                '    def mark_done(self) -> None:\n'
                '        pass\n',
                encoding="utf-8"
            )

            # Module 2: Functions
            (workspace / "utils.py").write_text(
                'import os\n\n'
                'def format_name(first: str, last: str = "") -> str:\n'
                '    return f"{first} {last}".strip()\n',
                encoding="utf-8"
            )

            repo_map = generate_repo_map(workspace)
            
            self.assertIn("models.py:", repo_map)
            self.assertIn("class Task:", repo_map)
            self.assertIn("def __init__(self, task_id: str, title: str, done: bool = False) -> None", repo_map)
            self.assertIn("def mark_done(self) -> None", repo_map)
            self.assertIn("utils.py:", repo_map)
            self.assertIn("def format_name(first: str, last: str = '') -> str", repo_map)

    def test_generate_repo_map_ignores_hidden_and_venv(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            
            # Valid root file
            (workspace / "app.py").write_text("def run(): pass\n", encoding="utf-8")

            # Files in ignored directories
            git_dir = workspace / ".git"
            git_dir.mkdir()
            (git_dir / "hook.py").write_text("def git_hook(): pass\n", encoding="utf-8")

            pycache_dir = workspace / "__pycache__"
            pycache_dir.mkdir()
            (pycache_dir / "cached.py").write_text("def cache(): pass\n", encoding="utf-8")

            venv_dir = workspace / ".venv" / "lib"
            venv_dir.mkdir(parents=True)
            (venv_dir / "dep.py").write_text("def library(): pass\n", encoding="utf-8")

            repo_map = generate_repo_map(workspace)
            
            self.assertIn("app.py:", repo_map)
            self.assertNotIn("hook.py", repo_map)
            self.assertNotIn("cached.py", repo_map)
            self.assertNotIn("dep.py", repo_map)


class TestLinterAndDependencies(unittest.TestCase):
    """Test suite for Fast Linter and Dependency Manager."""

    def test_fast_linter_detects_syntax_errors(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "broken.py").write_text("def broken(\n", encoding="utf-8")
            
            result = run_fast_linter(workspace)
            self.assertFalse(result.passed)
            self.assertGreaterEqual(len(result.errors), 1)
            self.assertIn("broken.py", result.errors[0])

    def test_fast_linter_passes_valid_code(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "good.py").write_text("def good(x: int) -> int:\n    return x * 2\n", encoding="utf-8")
            
            result = run_fast_linter(workspace)
            self.assertTrue(result.passed)
            self.assertEqual(len(result.errors), 0)

    def test_dependency_manager_extraction_and_mapping(self):
        sample_traceback = (
            "Traceback (most recent call last):\n"
            "  File 'main.py', line 1, in <module>\n"
            "    import yaml\n"
            "ModuleNotFoundError: No module named 'yaml'\n"
            "During handling...\n"
            "    from PIL import Image\n"
            "ModuleNotFoundError: No module named 'PIL'\n"
            "    import os\n"
        )
        missing = extract_missing_dependencies(sample_traceback)
        self.assertIn("pyyaml", missing)
        self.assertIn("pillow", missing)
        self.assertNotIn("os", missing)  # Standard library should be filtered

    def test_update_requirements_txt(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            req_file = workspace / "requirements.txt"
            req_file.write_text("fastapi>=0.100.0\n", encoding="utf-8")

            updated = update_requirements_txt(workspace, ["pyyaml", "fastapi"])
            self.assertTrue(updated)
            
            content = req_file.read_text(encoding="utf-8")
            self.assertIn("pyyaml", content)
            self.assertEqual(content.count("fastapi"), 1)  # No duplicates

    def test_validate_package_name_security(self):
        self.assertTrue(validate_package_name("pytest"))
        self.assertTrue(validate_package_name("pyyaml"))
        self.assertTrue(validate_package_name("scikit-learn"))
        self.assertTrue(validate_package_name("python_dotenv"))
        
        # Malicious / command injection names
        self.assertFalse(validate_package_name("; rm -rf /"))
        self.assertFalse(validate_package_name("pkg && whoami"))
        self.assertFalse(validate_package_name("pkg|cat"))
        self.assertFalse(validate_package_name("../secret"))

if __name__ == "__main__":
    unittest.main()
