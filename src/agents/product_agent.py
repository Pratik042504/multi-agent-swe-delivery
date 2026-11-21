from typing import Dict, Any
from .base import BaseAgent
from ..state import PRD, FeatureSpec, AcceptanceCriterion

class ProductAgent(BaseAgent):
    """Product Manager Agent: Translates raw user requests into actionable PRDs."""

    def __init__(self):
        super().__init__(name="ProductArchitect", role="Product Management & Specification Lead")

    def run(self, user_request: str) -> Dict[str, Any]:
        prompt = (
            f"Analyze the following user software request and construct a detailed PRD:\n"
            f"REQUEST: {user_request}\n"
            f"Define concrete features and Given/When/Then acceptance criteria."
        )

        def fallback() -> PRD:
            return PRD(
                title=f"Specification for: {user_request[:40]}",
                summary=f"Automated specification for '{user_request}'. Designed for high testability and modularity.",
                features=[
                    FeatureSpec(
                        name="Core Application Logic",
                        description="Primary business logic fulfilling the user requirement.",
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="AC-1",
                                scenario="Standard execution path",
                                given="Valid configuration and input parameters",
                                when="The service executes core operations",
                                then="It returns expected success status and deterministic output"
                            ),
                            AcceptanceCriterion(
                                id="AC-2",
                                scenario="Validation and error handling",
                                given="Invalid or missing inputs",
                                when="The service processes the request",
                                then="It handles errors gracefully with meaningful status codes"
                            )
                        ]
                    )
                ],
                non_functional_requirements=[
                    "100% pytest test compatibility",
                    "Modular structure with separation of concerns",
                    "Sub-second response time for standard unit operations"
                ]
            )

        prd_obj = self.invoke_structured(prompt, PRD, fallback)
        return prd_obj.model_dump()
