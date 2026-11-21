"""Surgical Diff Editor with AST syntax validation before disk writes."""

import ast
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

@dataclass
class DiffResult:
    """Result of a surgical diff edit operation."""
    success: bool
    file_path: Path
    error: Optional[str] = None
    syntax_error_details: Optional[Dict[str, Any]] = None
    diff: str = ""
    blocks_applied: int = 0
    blocks_failed: int = 0

def validate_python_syntax(code: str, filename: str = "<string>") -> Optional[Dict[str, Any]]:
    """Validates Python syntax using ast.parse().
    
    Returns None if valid, or a dictionary with error location details on SyntaxError.
    """
    try:
        ast.parse(code, filename=filename)
        return None
    except SyntaxError as e:
        snippet = ""
        if e.text:
            snippet = e.text.strip()
        elif e.lineno:
            lines = code.splitlines()
            if 0 < e.lineno <= len(lines):
                snippet = lines[e.lineno - 1].strip()
                
        return {
            "lineno": e.lineno or 1,
            "offset": e.offset or 0,
            "msg": e.msg or "Syntax error",
            "text": snippet
        }

def apply_search_replace(file_path: Path, blocks: List[Tuple[str, str]]) -> DiffResult:
    """Applies surgical search/replace blocks to a file.
    
    Before writing any .py file to disk, validates syntax via ast.parse().
    On SyntaxError, aborts the write and returns exact error location.
    
    Args:
        file_path: Target Path to modify or create.
        blocks: List of (search_block, replace_block) tuples.
        
    Returns:
        DiffResult detailing success, applied blocks, diff, or syntax error details.
    """
    file_path = Path(file_path)
    
    # Read existing content if present
    if file_path.exists():
        try:
            original_content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return DiffResult(
                success=False,
                file_path=file_path,
                error=f"Failed to read file {file_path}: {e}"
            )
    else:
        # If file does not exist, can only be created if first block is empty search
        if len(blocks) == 1 and blocks[0][0] == "":
            original_content = ""
        else:
            return DiffResult(
                success=False,
                file_path=file_path,
                error=f"File not found: {file_path}. Cannot apply search/replace on non-existent file."
            )

    current_content = original_content
    blocks_applied = 0
    blocks_failed = 0

    for idx, (search, replace) in enumerate(blocks):
        if not search:
            # Empty search with empty original content = initial creation
            if original_content == "" and idx == 0:
                current_content = replace
                blocks_applied += 1
                continue
            else:
                blocks_failed += 1
                return DiffResult(
                    success=False,
                    file_path=file_path,
                    error=f"Empty search block at index {idx} not permitted on existing content",
                    blocks_applied=blocks_applied,
                    blocks_failed=blocks_failed
                )

        # 1. Exact match attempt
        if search in current_content:
            current_content = current_content.replace(search, replace, 1)
            blocks_applied += 1
            continue

        # 2. Normalized line endings fallback (\r\n vs \n)
        norm_search = search.replace("\r\n", "\n")
        norm_content = current_content.replace("\r\n", "\n")
        if norm_search in norm_content:
            norm_replace = replace.replace("\r\n", "\n")
            current_content = norm_content.replace(norm_search, norm_replace, 1)
            blocks_applied += 1
            continue

        # 3. Trailing whitespace-normalized fallback
        stripped_search = "\n".join(line.rstrip() for line in norm_search.splitlines())
        stripped_content = "\n".join(line.rstrip() for line in norm_content.splitlines())
        if stripped_search in stripped_content:
            # Reconstruct replacement while preserving indentation
            current_content = stripped_content.replace(stripped_search, replace.replace("\r\n", "\n"), 1)
            blocks_applied += 1
            continue

        # Block failed to match
        blocks_failed += 1
        return DiffResult(
            success=False,
            file_path=file_path,
            error=f"Search block {idx + 1} not found in {file_path.name}:\n'''\n{search[:200]}\n'''",
            blocks_applied=blocks_applied,
            blocks_failed=blocks_failed
        )

    # Validate Python syntax before writing to disk
    if file_path.suffix.lower() == ".py":
        syntax_err = validate_python_syntax(current_content, filename=str(file_path))
        if syntax_err:
            # Abort write to preserve file on disk
            return DiffResult(
                success=False,
                file_path=file_path,
                error=(
                    f"SyntaxError: {syntax_err['msg']} at line {syntax_err['lineno']}, "
                    f"offset {syntax_err['offset']}: '{syntax_err['text']}'"
                ),
                syntax_error_details=syntax_err,
                blocks_applied=blocks_applied,
                blocks_failed=blocks_failed
            )

    # Write validated content to disk
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(current_content, encoding="utf-8")
    except Exception as e:
        return DiffResult(
            success=False,
            file_path=file_path,
            error=f"Disk write error for {file_path}: {e}",
            blocks_applied=blocks_applied,
            blocks_failed=blocks_failed
        )

    # Compute unified diff representation
    old_lines = original_content.splitlines(keepends=True)
    new_lines = current_content.splitlines(keepends=True)
    unified_diff_str = "".join(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{file_path.name}",
        tofile=f"b/{file_path.name}"
    ))

    return DiffResult(
        success=True,
        file_path=file_path,
        diff=unified_diff_str,
        blocks_applied=blocks_applied,
        blocks_failed=0
    )

def apply_unified_diff(file_path: Path, patch_text: str) -> DiffResult:
    """Applies a standard unified diff patch to a file with AST validation."""
    file_path = Path(file_path)
    if not file_path.exists():
        return DiffResult(
            success=False,
            file_path=file_path,
            error=f"Target file for patch not found: {file_path}"
        )

    original_content = file_path.read_text(encoding="utf-8")
    original_lines = original_content.splitlines()

    patch_lines = patch_text.splitlines()
    hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    new_lines = list(original_lines)
    offset = 0
    in_hunk = False
    blocks_applied = 0

    # Parse unified diff hunks
    hunks = []
    current_hunk = []
    for line in patch_lines:
        if line.startswith("@@"):
            if current_hunk:
                hunks.append(current_hunk)
            current_hunk = [line]
        elif current_hunk:
            current_hunk.append(line)
    if current_hunk:
        hunks.append(current_hunk)

    if not hunks:
        return DiffResult(
            success=False,
            file_path=file_path,
            error="No valid diff hunks (@@ ... @@) found in patch"
        )

    current_lines = list(original_lines)
    line_offset = 0

    for hunk in hunks:
        header = hunk[0]
        match = hunk_header_re.match(header)
        if not match:
            continue

        orig_start = int(match.group(1)) - 1  # 0-indexed
        orig_count = int(match.group(2)) if match.group(2) else 1

        # Search for context match around orig_start + line_offset
        search_lines = [l[1:] for l in hunk[1:] if l.startswith(" ") or l.startswith("-")]
        replacement_lines = [l[1:] for l in hunk[1:] if l.startswith(" ") or l.startswith("+")]

        search_block = "\n".join(search_lines)
        replace_block = "\n".join(replacement_lines)

        res = apply_search_replace(file_path, [(search_block, replace_block)])
        if not res.success:
            return res
        blocks_applied += 1

    return DiffResult(
        success=True,
        file_path=file_path,
        diff=patch_text,
        blocks_applied=blocks_applied,
        blocks_failed=0
    )
