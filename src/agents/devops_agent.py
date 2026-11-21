from pathlib import Path
from typing import Dict, Any
from .base import BaseAgent
from ..state import DeliveryArtifacts
from ..tools.cicd_generator import (
    generate_dockerfile,
    generate_dockerignore,
    generate_github_workflow,
    generate_docker_compose
)
from ..tools.git_tools import GitManager
from ..config import settings

class DevOpsAgent(BaseAgent):
    """DevOps & Delivery Agent: Packages the software, configures CI/CD, and commits to Git."""

    def __init__(self):
        super().__init__(name="DevOpsLead", role="Staff Site Reliability & Delivery Engineer")

    def run(
        self,
        workspace_dir: Path,
        app_name: str,
        entrypoint: str,
        prd_summary: str,
        test_summary: str,
        security_summary: str
    ) -> Dict[str, Any]:
        
        # 1. Generate Container and CI/CD assets
        dockerfile = generate_dockerfile(app_entrypoint=entrypoint)
        dockerignore = generate_dockerignore()
        workflow = generate_github_workflow(app_name=app_name, registry=settings.docker_registry)
        docker_compose = generate_docker_compose(app_name=app_name)

        # 2. Write deployment manifests into workspace
        (workspace_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        (workspace_dir / ".dockerignore").write_text(dockerignore, encoding="utf-8")
        (workspace_dir / "docker-compose.yml").write_text(docker_compose, encoding="utf-8")
        
        gh_workflow_dir = workspace_dir / ".github" / "workflows"
        gh_workflow_dir.mkdir(parents=True, exist_ok=True)
        (gh_workflow_dir / "ci-cd.yml").write_text(workflow, encoding="utf-8")

        # 3. Git Operations: Branch and commit
        git_mgr = GitManager(
            workspace_dir,
            author_name=settings.git_author_name,
            author_email=settings.git_author_email
        )
        branch_name = f"feature/{app_name}-automated-delivery"
        git_mgr.checkout_feature_branch(branch_name)
        commit_sha = git_mgr.commit_changes(f"feat({app_name}): automated generation and delivery pipeline")
        
        pr_payload = git_mgr.generate_pr_payload(
            title=f"feat({app_name}): Automated Delivery Release",
            prd_summary=prd_summary,
            test_summary=test_summary,
            security_summary=security_summary
        )

        release_notes = (
            f"Release Package for {app_name}:\n"
            f"- Branch: {branch_name}\n"
            f"- Commit: {commit_sha}\n"
            f"- Dockerfile & docker-compose created.\n"
            f"- GitHub Actions CI/CD pipeline generated at .github/workflows/ci-cd.yml\n"
            f"- Pull Request payload prepared."
        )

        artifacts = DeliveryArtifacts(
            dockerfile=dockerfile,
            dockerignore=dockerignore,
            github_workflow=workflow,
            deployment_manifest=docker_compose,
            release_notes=release_notes
        )
        return artifacts.model_dump()
