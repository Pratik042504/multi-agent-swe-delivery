from typing import Dict, Any, Optional
from .base import BaseAgent
from ..state import ArchitecturePlan

class ArchitectAgent(BaseAgent):
    """System Architect Agent: Designs component topology, file structure, and test boundaries."""

    def __init__(self):
        super().__init__(name="SystemArchitect", role="Principal Software Architect")

    def run(self, user_request: str, prd_dict: Dict[str, Any], repo_map: Optional[str] = None) -> Dict[str, Any]:
        prompt_parts = [
            f"Given the user request and PRD, design the architecture and file layout:\n"
            f"REQUEST: {user_request}\n"
            f"PRD: {prd_dict.get('summary', '')}\n"
            f"FEATURES: {[f.get('name') for f in prd_dict.get('features', [])]}\n"
        ]

        if repo_map and repo_map.strip() and "No Python source files" not in repo_map:
            prompt_parts.append(f"\n{repo_map}\n(Align new components with existing architecture and module signatures)\n")

        prompt_parts.append("Design a clean, modular Python codebase with explicit test files.")
        prompt = "\n".join(prompt_parts)

        def fallback() -> ArchitecturePlan:
            return ArchitecturePlan(
                system_overview="Modular architecture separating domain logic, interfaces, and test suites.",
                components=["Domain Logic Service", "Data Model Validator", "Test Suite"],
                dependencies=["pytest", "pydantic"],
                source_files_planned=["service.py", "main.py"],
                test_files_planned=["test_service.py"],
                execution_entrypoint="main.py"
            )

        arch_obj = self.invoke_structured(prompt, ArchitecturePlan, fallback)
        return arch_obj.model_dump()
