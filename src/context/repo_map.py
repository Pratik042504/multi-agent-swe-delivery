"""AST Repository Map generator for workspace structure and symbol extraction."""

import ast
import os
from pathlib import Path
from typing import List, Dict, Optional, Set

IGNORE_DIRS: Set[str] = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".pytest_cache",
    ".coverage",
    "htmlcov",
    "dist",
    "build",
    ".egg-info"
}

def _format_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Formats a function/method signature including parameter types and return type."""
    is_async = isinstance(node, ast.AsyncFunctionDef)
    prefix = "async def " if is_async else "def "
    
    # Format arguments
    args_list = []
    
    # Defaults alignment: defaults are aligned with the end of posargs
    defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults)
    
    for arg, default in zip(node.args.args, defaults):
        arg_str = arg.arg
        if arg.annotation:
            try:
                arg_str += f": {ast.unparse(arg.annotation)}"
            except Exception:
                pass
        if default is not None:
            try:
                arg_str += f" = {ast.unparse(default)}"
            except Exception:
                pass
        args_list.append(arg_str)

    # Varargs (*args)
    if node.args.vararg:
        vararg_str = f"*{node.args.vararg.arg}"
        if node.args.vararg.annotation:
            try:
                vararg_str += f": {ast.unparse(node.args.vararg.annotation)}"
            except Exception:
                pass
        args_list.append(vararg_str)

    # Kwonly args
    kw_defaults = dict(zip([a.arg for a in node.args.kwonlyargs], node.args.kw_defaults))
    for arg in node.args.kwonlyargs:
        kw_str = arg.arg
        if arg.annotation:
            try:
                kw_str += f": {ast.unparse(arg.annotation)}"
            except Exception:
                pass
        default = kw_defaults.get(arg.arg)
        if default is not None:
            try:
                kw_str += f" = {ast.unparse(default)}"
            except Exception:
                pass
        args_list.append(kw_str)

    # Kwarg (**kwargs)
    if node.args.kwarg:
        kwarg_str = f"**{node.args.kwarg.arg}"
        if node.args.kwarg.annotation:
            try:
                kwarg_str += f": {ast.unparse(node.args.kwarg.annotation)}"
            except Exception:
                pass
        args_list.append(kwarg_str)

    sig = f"{prefix}{node.name}({', '.join(args_list)})"
    
    if node.returns:
        try:
            sig += f" -> {ast.unparse(node.returns)}"
        except Exception:
            pass
            
    return sig

def extract_file_ast_map(file_path: Path, rel_path: str) -> Optional[str]:
    """Parses a Python file and extracts imports, classes, and method signatures."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return None

    try:
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError:
        return f"{rel_path}: (SyntaxError while parsing file)"

    lines = [f"{rel_path}:"]

    # Module docstring
    docstring = ast.get_docstring(tree)
    if docstring:
        first_line = docstring.strip().splitlines()[0]
        lines.append(f"  # Doc: {first_line}")

    # Top-level imports
    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name_str = alias.name + (f" as {alias.asname}" if alias.asname else "")
                imports.append(f"import {name_str}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names_str = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in node.names)
            imports.append(f"from {mod} import {names_str}")

    if imports:
        # Group or limit imports to keep map concise
        for imp in imports[:8]:
            lines.append(f"  {imp}")
        if len(imports) > 8:
            lines.append(f"  # ... and {len(imports) - 8} more imports")

    # Classes and Functions
    has_symbols = False
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            has_symbols = True
            bases = []
            for b in node.bases:
                try:
                    bases.append(ast.unparse(b))
                except Exception:
                    pass
            base_str = f"({', '.join(bases)})" if bases else ""
            lines.append(f"  class {node.name}{base_str}:")
            
            class_doc = ast.get_docstring(node)
            if class_doc:
                first_doc = class_doc.strip().splitlines()[0]
                lines.append(f"    # {first_doc}")

            # Methods
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig = _format_function_signature(item)
                    lines.append(f"    {sig}")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            has_symbols = True
            sig = _format_function_signature(node)
            lines.append(f"  {sig}")

    if not imports and not has_symbols:
        lines.append("  (empty or variable definitions only)")

    return "\n".join(lines)

def generate_repo_map(workspace_path: Path, max_depth: int = 5, max_chars: int = 8000) -> str:
    """Scans the workspace directory and constructs a compact AST repository map.
    
    Args:
        workspace_path: Root Path of the workspace.
        max_depth: Maximum directory recursion depth.
        max_chars: Maximum character limit for prompt injection.
        
    Returns:
        Formatted multi-line string representing the codebase structure and signatures.
    """
    workspace_path = Path(workspace_path)
    if not workspace_path.exists():
        return "Workspace does not exist."

    file_maps = []
    
    for root, dirs, files in os.walk(workspace_path):
        # Filter out ignored directories in-place
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        
        rel_root = Path(root).relative_to(workspace_path)
        if len(rel_root.parts) > max_depth:
            continue

        for file in sorted(files):
            if file.endswith(".py"):
                full_path = Path(root) / file
                rel_path = full_path.relative_to(workspace_path).as_posix()
                file_ast = extract_file_ast_map(full_path, rel_path)
                if file_ast:
                    file_maps.append(file_ast)

    if not file_maps:
        return "No Python source files found in workspace."

    header = "=== AST Repository Map (Codebase Structure & API Signatures) ===\n"
    combined = header + "\n\n".join(file_maps)

    if len(combined) > max_chars:
        combined = combined[:max_chars - 60] + "\n\n... [Repository Map truncated for token budget]"

    return combined
