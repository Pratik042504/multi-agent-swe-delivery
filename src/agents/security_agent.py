"""Augmented Security Agent combining AST static analysis with Bandit and pip-audit."""

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from .base import BaseAgent
from ..state import AuditReport, SecurityReview, SecurityFinding

class SecurityAgent(BaseAgent):
    """Security & Code Quality Agent: Scans for AST anti-patterns, Bandit SAST vulnerabilities, and dependency CVEs."""

    def __init__(self):
        super().__init__(name="SecurityAuditor", role="Principal AppSec & Code Review Engineer")

    def run(self, source_files: Dict[str, str], workspace_path: Optional[Path] = None) -> Dict[str, Any]:
        """Executes multi-layered security analysis: AST analysis + Bandit SAST + pip-audit."""
        findings: List[SecurityFinding] = []
        bandit_data: Optional[Dict[str, Any]] = None
        pip_audit_data: Optional[Dict[str, Any]] = None

        # Layer 1: AST Static Analysis
        for filename, content in source_files.items():
            if not filename.endswith(".py"):
                continue

            # Check dangerous built-ins
            try:
                tree = ast.parse(content, filename=filename)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        if node.func.id in ("eval", "exec"):
                            findings.append(SecurityFinding(
                                severity="CRITICAL",
                                file=filename,
                                issue=f"Use of dangerous built-in '{node.func.id}' detected.",
                                recommendation=f"Replace '{node.func.id}' with a safe parser or explicit mapping."
                            ))
            except SyntaxError as e:
                findings.append(SecurityFinding(
                    severity="CRITICAL",
                    file=filename,
                    issue=f"Python syntax error: {str(e)}",
                    recommendation="Fix syntax to allow parsing."
                ))

            # Secret leakage heuristic
            secret_patterns = [
                (r"(?i)password\s*=\s*['\"][^'\"]{4,}['\"]", "Hardcoded password assignment"),
                (r"(?i)api[_-]?key\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]", "Possible hardcoded API key")
            ]
            for pattern, msg in secret_patterns:
                if re.search(pattern, content):
                    findings.append(SecurityFinding(
                        severity="HIGH",
                        file=filename,
                        issue=msg,
                        recommendation="Load secrets from environment variables or a secrets manager."
                    ))

        # Layer 2: Bandit Static SAST Scanner (if workspace and binary are present)
        if workspace_path and Path(workspace_path).exists():
            bandit_bin = shutil.which("bandit")
            if bandit_bin:
                try:
                    cmd = [bandit_bin, "-r", str(workspace_path), "-f", "json", "-q"]
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                    if proc.stdout:
                        bandit_data = json.loads(proc.stdout)
                        for item in bandit_data.get("results", []):
                            sev = item.get("issue_severity", "MEDIUM").upper()
                            file_rel = Path(item.get("filename", "")).name
                            findings.append(SecurityFinding(
                                severity=sev,
                                file=file_rel,
                                issue=f"[Bandit {item.get('test_id')}] {item.get('issue_text')} (line {item.get('line_number')})",
                                recommendation=item.get("more_info", "Review and remediate according to Bandit guidelines.")
                            ))
                except Exception as e:
                    bandit_data = {"status": "error", "message": str(e)}
            else:
                bandit_data = {"status": "skipped", "message": "bandit binary not available in PATH"}

        # Layer 3: pip-audit Dependency CVE Scanner (if workspace and binary are present)
        if workspace_path and Path(workspace_path).exists():
            req_file = Path(workspace_path) / "requirements.txt"
            pip_audit_bin = shutil.which("pip-audit")
            if pip_audit_bin and req_file.exists():
                try:
                    cmd = [pip_audit_bin, "-r", str(req_file), "-f", "json"]
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                    if proc.stdout:
                        pip_audit_data = json.loads(proc.stdout)
                        for dep in pip_audit_data.get("dependencies", []):
                            for vuln in dep.get("vulns", []):
                                findings.append(SecurityFinding(
                                    severity="HIGH",
                                    file="requirements.txt",
                                    issue=f"[pip-audit {vuln.get('id')}] {dep.get('name')}=={dep.get('version')}: {vuln.get('description', '')[:120]}",
                                    recommendation=f"Upgrade {dep.get('name')} to safe version: {', '.join(vuln.get('fix_versions', ['latest']))}"
                                ))
                except Exception as e:
                    pip_audit_data = {"status": "error", "message": str(e)}
            else:
                pip_audit_data = {
                    "status": "skipped",
                    "message": "pip-audit binary or requirements.txt not available"
                }

        # Calculate score and overall approval status
        has_critical = any(f.severity in ("CRITICAL", "HIGH") for f in findings)
        approved = not has_critical
        score = 9.8 if approved else max(1.0, 9.8 - (len(findings) * 1.5))

        summary = (
            "Security and Quality Gate Passed. Code is clean of severe vulnerabilities."
            if approved else
            f"Security Gate Rejected: {len(findings)} security findings identified."
        )

        audit_report = AuditReport(
            passed=approved,
            quality_score=round(score, 1),
            summary=summary,
            findings=findings,
            bandit_scan=bandit_data,
            pip_audit_scan=pip_audit_data
        )

        # Include backwards-compatible approved field
        res_dict = audit_report.model_dump()
        res_dict["approved"] = approved
        return res_dict
