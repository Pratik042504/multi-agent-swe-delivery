"""Structured Agent State models, Pydantic schemas, and context compaction reducers."""

from typing import TypedDict, Optional, Dict, List, Any, Annotated

def extract_one_line_summary(error_text: str) -> str:
    """Extracts a succinct single-line summary from an error or traceback."""
    if not error_text:
        return "Unknown error"
    lines = [line.strip() for line in error_text.strip().splitlines() if line.strip()]
    if not lines:
        return "Empty error traceback"

    # Search in reverse for typical exception / failure indicators
    for line in reversed(lines):
        if any(keyword in line for keyword in [
            "Error:", "Exception:", "FAILED", "AssertionError", "SyntaxError",
            "ModuleNotFoundError", "ImportError", "KeyError", "ValueError"
        ]):
            return line

    return lines[-1]

def reduce_error_logs(current: Optional[List[str]], new_errors: Optional[List[str]]) -> List[str]:
    """Custom state reducer that preserves only the most recent full traceback

    plus concise single-line summaries of previous failed attempts to prevent
    LLM prompt context bloat.
    """
    combined: List[str] = list(current or [])
    if new_errors:
        for err in new_errors:
            if err and err not in combined:
                combined.append(err)

    if not combined:
        return []

    if len(combined) == 1:
        return combined

    # Compact all previous attempts into 1-line summaries; retain latest full traceback
    compacted: List[str] = []
    for idx, err in enumerate(combined[:-1], start=1):
        summary = extract_one_line_summary(err)
        compacted.append(f"[Attempt #{idx} Failed]: {summary}")

    compacted.append(combined[-1])
    return compacted

try:
    from pydantic import BaseModel, Field

    class AcceptanceCriterion(BaseModel):
        id: str
        scenario: str
        given: str
        when: str
        then: str

    class FeatureSpec(BaseModel):
        name: str
        description: str
        acceptance_criteria: List[AcceptanceCriterion] = Field(default_factory=list)

    class PRDSpec(BaseModel):
        """Structured Product Requirements Document specification."""
        title: str
        summary: str
        features: List[FeatureSpec] = Field(default_factory=list)
        non_functional_requirements: List[str] = Field(default_factory=list)

    # Alias PRD to PRDSpec for backward compatibility
    class PRD(PRDSpec):
        pass

    class ArchitecturePlan(BaseModel):
        """Structured system topology, components, and file placement plan."""
        system_overview: str
        components: List[str] = Field(default_factory=list)
        dependencies: List[str] = Field(default_factory=list)
        source_files_planned: List[str] = Field(default_factory=list)
        test_files_planned: List[str] = Field(default_factory=list)
        execution_entrypoint: str = "main.py"

    class CodePatch(BaseModel):
        """Structured code patches and generated file payloads."""
        files: Dict[str, str] = Field(default_factory=dict, description="Relative path -> file content")
        test_files: Dict[str, str] = Field(default_factory=dict, description="Relative path -> test content")
        diffs: Dict[str, str] = Field(default_factory=dict, description="Relative path -> unified diff")
        implementation_notes: str = ""

    class TestExecutionResult(BaseModel):
        passed: bool
        summary: str
        total_tests: int = 0
        passed_tests: int = 0
        failed_tests: int = 0
        stdout: str = ""
        stderr: str = ""
        errors: List[str] = Field(default_factory=list)

    class SecurityFinding(BaseModel):
        severity: str  # CRITICAL, HIGH, MEDIUM, LOW
        file: str
        issue: str
        recommendation: str

    class AuditReport(BaseModel):
        """Structured security audit and quality report."""
        passed: bool
        quality_score: float  # 0.0 - 10.0
        summary: str
        findings: List[SecurityFinding] = Field(default_factory=list)
        bandit_scan: Optional[Dict[str, Any]] = None
        pip_audit_scan: Optional[Dict[str, Any]] = None

    # Alias SecurityReview to AuditReport for backward compatibility
    class SecurityReview(AuditReport):
        approved: bool = True

        def __init__(self, **data):
            if "approved" in data and "passed" not in data:
                data["passed"] = data["approved"]
            elif "passed" in data and "approved" not in data:
                data["approved"] = data["passed"]
            super().__init__(**data)

    class DeliveryArtifacts(BaseModel):
        dockerfile: str
        dockerignore: str
        github_workflow: str
        deployment_manifest: Optional[str] = None
        release_notes: str

except ImportError:
    from dataclasses import dataclass, field, asdict

    class BaseModel:
        def model_dump(self) -> dict:
            return asdict(self)
        @classmethod
        def model_validate(cls, data: dict):
            return cls(**data)

    @dataclass
    class AcceptanceCriterion(BaseModel):
        id: str
        scenario: str
        given: str
        when: str
        then: str

    @dataclass
    class FeatureSpec(BaseModel):
        name: str
        description: str
        acceptance_criteria: List[AcceptanceCriterion] = field(default_factory=list)

    @dataclass
    class PRDSpec(BaseModel):
        title: str
        summary: str
        features: List[FeatureSpec] = field(default_factory=list)
        non_functional_requirements: List[str] = field(default_factory=list)

    @dataclass
    class PRD(PRDSpec):
        pass

    @dataclass
    class ArchitecturePlan(BaseModel):
        system_overview: str
        components: List[str] = field(default_factory=list)
        dependencies: List[str] = field(default_factory=list)
        source_files_planned: List[str] = field(default_factory=list)
        test_files_planned: List[str] = field(default_factory=list)
        execution_entrypoint: str = "main.py"

    @dataclass
    class CodePatch(BaseModel):
        files: Dict[str, str] = field(default_factory=dict)
        test_files: Dict[str, str] = field(default_factory=dict)
        diffs: Dict[str, str] = field(default_factory=dict)
        implementation_notes: str = ""

    @dataclass
    class TestExecutionResult(BaseModel):
        passed: bool
        summary: str
        total_tests: int = 0
        passed_tests: int = 0
        failed_tests: int = 0
        stdout: str = ""
        stderr: str = ""
        errors: List[str] = field(default_factory=list)

    @dataclass
    class SecurityFinding(BaseModel):
        severity: str
        file: str
        issue: str
        recommendation: str

    @dataclass
    class AuditReport(BaseModel):
        passed: bool
        quality_score: float
        summary: str
        findings: List[SecurityFinding] = field(default_factory=list)
        bandit_scan: Optional[Dict[str, Any]] = None
        pip_audit_scan: Optional[Dict[str, Any]] = None

    @dataclass
    class SecurityReview(AuditReport):
        approved: bool = True

    @dataclass
    class DeliveryArtifacts(BaseModel):
        dockerfile: str
        dockerignore: str
        github_workflow: str
        release_notes: str
        deployment_manifest: Optional[str] = None

class AgentState(TypedDict, total=False):
    """The central state dictionary passed across all nodes in the LangGraph."""
    session_id: str
    thread_id: str
    run_id: str
    user_request: str
    
    # 1. Product Agent Output
    prd: Optional[Dict[str, Any]]
    
    # 2. Architect Agent Output
    architecture: Optional[Dict[str, Any]]
    
    # 3. Coder Agent Output
    source_files: Dict[str, str]
    test_files: Dict[str, str]
    code_patch: Optional[Dict[str, Any]]
    
    # 4. QA Agent Output
    test_results: Optional[Dict[str, Any]]
    error_logs: Annotated[List[str], reduce_error_logs]
    
    # 5. Security Agent Output
    security_review: Optional[Dict[str, Any]]
    audit_report: Optional[Dict[str, Any]]
    
    # 6. DevOps Agent Output
    delivery_artifacts: Optional[Dict[str, Any]]
    
    # Routing, Loops & Gate Controls
    iteration_count: int
    max_iterations: int
    status: str
    human_approved: bool
    
    # Telemetry
    cost_usd: float
    token_usage: Dict[str, int]
    audit_log: List[Dict[str, str]]
