"""Dynamic Dependency Manager for autonomous traceback parsing and package resolution."""

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Set, Optional

# Mapping of common top-level import names to PyPI distribution package names
IMPORT_TO_PYPI_MAP: Dict[str, str] = {
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "git": "gitpython",
    "jwt": "pyjwt",
    "jose": "python-jose",
    "serial": "pyserial",
    "magic": "python-magic",
    "Crypto": "pycryptodome",
    "dateutil": "python-dateutil",
    "google.generativeai": "google-generativeai",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "requests": "requests",
    "httpx": "httpx",
    "aiohttp": "aiohttp",
    "pytest": "pytest"
}

# Standard library modules that must never be pip-installed
STD_LIB_MODULES: Set[str] = {
    "sys", "os", "json", "time", "re", "math", "typing", "pathlib", "ast",
    "unittest", "difflib", "subprocess", "dataclasses", "collections",
    "itertools", "functools", "http", "urllib", "threading", "queue",
    "tempfile", "shutil", "io", "zipfile", "socket", "uuid", "abc",
    "contextlib", "copy", "datetime", "enum", "logging", "random",
    "string", "traceback", "types", "warnings", "weakref", "shlex",
    "hashlib", "hmac", "secrets", "select", "selectors", "asyncio"
}

PACKAGE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}[a-zA-Z0-9]$")

@dataclass
class DependencyResult:
    """Result of dependency parsing and installation."""
    missing_packages: List[str]
    installed_packages: List[str]
    failed_packages: List[str]
    requirements_updated: bool
    summary: str

def validate_package_name(package_name: str) -> bool:
    """Validates that a package name conforms to safe PyPI naming conventions."""
    if not package_name or len(package_name) > 64:
        return False
    # Single character package names are rare but valid (e.g. 'c')
    if len(package_name) == 1 and package_name.isalnum():
        return True
    return bool(PACKAGE_NAME_REGEX.match(package_name))

def extract_missing_dependencies(error_output: str) -> List[str]:
    """Parses tracebacks and error strings for missing module/import dependencies.
    
    Args:
        error_output: Stderr or stdout containing Python exception tracebacks.
        
    Returns:
        List of unique, normalized PyPI package names.
    """
    patterns = [
        re.compile(r"ModuleNotFoundError:\s+No module named '([^']+)'"),
        re.compile(r"ImportError:\s+cannot import name .+ from '([^']+)'"),
        re.compile(r"ImportError:\s+No module named '([^']+)'"),
        re.compile(r"No module named\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?")
    ]

    found_raw_modules: Set[str] = set()

    for pat in patterns:
        for match in pat.finditer(error_output):
            raw_mod = match.group(1).strip()
            # If dotted path (e.g. 'google.generativeai' or 'foo.bar'), check top-level or full
            if raw_mod in IMPORT_TO_PYPI_MAP:
                found_raw_modules.add(raw_mod)
            else:
                top_level = raw_mod.split(".")[0]
                found_raw_modules.add(top_level)

    # Normalize to PyPI names and filter standard library
    normalized_packages: List[str] = []
    for mod in found_raw_modules:
        if mod in STD_LIB_MODULES:
            continue
        pypi_name = IMPORT_TO_PYPI_MAP.get(mod, mod.lower().replace("_", "-"))
        if validate_package_name(pypi_name) and pypi_name not in normalized_packages:
            normalized_packages.append(pypi_name)

    return normalized_packages

def update_requirements_txt(workspace_path: Path, packages: List[str]) -> bool:
    """Updates requirements.txt in the workspace with missing packages.
    
    Returns True if requirements.txt was modified, False otherwise.
    """
    if not packages:
        return False

    req_file = workspace_path / "requirements.txt"
    existing_lines: List[str] = []
    existing_packages: Set[str] = set()

    if req_file.exists():
        try:
            content = req_file.read_text(encoding="utf-8")
            existing_lines = content.splitlines()
            for line in existing_lines:
                clean = line.strip().split("==")[0].split(">=")[0].split("<=")[0].strip().lower()
                if clean:
                    existing_packages.add(clean)
        except Exception:
            pass

    to_add = [pkg for pkg in packages if pkg.lower() not in existing_packages]
    if not to_add:
        return False

    new_lines = list(existing_lines)
    for pkg in to_add:
        new_lines.append(pkg)

    try:
        req_file.write_text("\n".join(new_lines).strip() + "\n", encoding="utf-8")
        return True
    except Exception:
        return False

def install_package_in_sandbox(package_name: str, timeout: int = 30) -> bool:
    """Attempts to pip install a package into the current Python environment."""
    if not validate_package_name(package_name):
        return False

    cmd = [sys.executable, "-m", "pip", "install", package_name]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return proc.returncode == 0
    except Exception:
        return False

def resolve_and_update_dependencies(
    workspace_path: Path,
    error_output: str,
    auto_install: bool = True,
    timeout: int = 30
) -> DependencyResult:
    """Analyzes error output, updates requirements.txt, and optionally installs packages.
    
    Args:
        workspace_path: Root of the target workspace.
        error_output: Output from test failure containing possible ImportError.
        auto_install: Whether to attempt pip install in the environment.
        timeout: Timeout per pip install operation.
        
    Returns:
        DependencyResult detailing missing, installed, and failed packages.
    """
    missing = extract_missing_dependencies(error_output)
    if not missing:
        return DependencyResult(
            missing_packages=[],
            installed_packages=[],
            failed_packages=[],
            requirements_updated=False,
            summary="No missing dependencies detected in traceback."
        )

    req_updated = update_requirements_txt(workspace_path, missing)
    installed: List[str] = []
    failed: List[str] = []

    if auto_install:
        for pkg in missing:
            ok = install_package_in_sandbox(pkg, timeout=timeout)
            if ok:
                installed.append(pkg)
            else:
                failed.append(pkg)

    summary = (
        f"Detected missing packages: {', '.join(missing)}. "
        f"Installed: {len(installed)}, Failed/Offline: {len(failed)}. "
        f"requirements.txt updated: {req_updated}."
    )

    return DependencyResult(
        missing_packages=missing,
        installed_packages=installed,
        failed_packages=failed,
        requirements_updated=req_updated,
        summary=summary
    )
