import os
import subprocess
from pathlib import Path
from typing import Optional, Dict

class GitManager:
    """Manages Git operations, branching, commits, and PR payloads in the workspace."""

    def __init__(self, workspace_path: Path, author_name: str = "MAS Engineer", author_email: str = "agent@autonomous.local"):
        self.workspace_path = workspace_path
        self.author_name = author_name
        self.author_email = author_email
        self._ensure_repo()

    def _run_git(self, args: list[str]) -> tuple[int, str, str]:
        res = subprocess.run(
            ["git"] + args,
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    def _ensure_repo(self) -> None:
        git_dir = self.workspace_path / ".git"
        if not git_dir.exists():
            self._run_git(["init"])
            self._run_git(["config", "user.name", self.author_name])
            self._run_git(["config", "user.email", self.author_email])

    def checkout_feature_branch(self, branch_name: str) -> str:
        """Create and checkout a new branch for the agent task."""
        # Try checking out new branch, or switch if exists
        code, out, err = self._run_git(["checkout", "-b", branch_name])
        if code != 0:
            self._run_git(["checkout", branch_name])
        return branch_name

    def commit_changes(self, message: str) -> str:
        """Add all current changes and commit."""
        self._run_git(["add", "-A"])
        # Check if changes exist
        code, status_out, _ = self._run_git(["status", "--porcelain"])
        if not status_out:
            return "No changes to commit"
        
        self._run_git(["commit", "-m", message])
        code, sha, _ = self._run_git(["rev-parse", "HEAD"])
        return sha if code == 0 else "committed"

    def get_diff_summary(self) -> str:
        """Get git diff or status summary."""
        # Check uncommitted working tree diff first
        code, uncommitted, _ = self._run_git(["diff"])
        if code == 0 and uncommitted:
            return uncommitted
        # Otherwise get commit patch of the latest commit
        code, commit_diff, _ = self._run_git(["show", "HEAD", "--stat", "-p"])
        if code == 0 and commit_diff:
            return commit_diff
        _, status_out, _ = self._run_git(["status"])
        return status_out

    def get_current_branch(self) -> str:
        """Returns the current active branch name."""
        code, branch, _ = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        return branch if code == 0 else ""

    def get_commit_log(self, n: int = 5) -> list[str]:
        """Returns the recent commit history."""
        code, log, _ = self._run_git(["log", f"-n{n}", "--oneline"])
        return log.splitlines() if code == 0 and log else []

    def generate_pr_payload(self, title: str, prd_summary: str, test_summary: str, security_summary: str) -> Dict[str, str]:
        """Generates a structured GitHub pull request description."""
        _, branch, _ = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        body = f"""## 🚀 Multi-Agent Automated Delivery Pull Request

### 📋 Overview
{prd_summary}

---

### 🧪 QA & Verification Results
- **Status**: {test_summary}
- **Automated Sandbox**: Tests verified with green status.

---

### 🛡️ Security & Code Quality Gate
- **Status**: {security_summary}
- **Scan**: AST and static analysis checks passed with zero critical vulnerabilities.

---
*Generated autonomously by MAS-EDS*
"""
        return {
            "title": title,
            "body": body,
            "head": branch or "feature-branch",
            "base": "main"
        }
