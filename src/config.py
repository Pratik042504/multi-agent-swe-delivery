import os
import tempfile
from pathlib import Path

IS_VERCEL = os.getenv("VERCEL") == "1" or os.getenv("AWS_LAMBDA_FUNCTION_NAME") is not None
DEFAULT_WORKSPACE_BASE = str(Path(tempfile.gettempdir()) / "workspaces") if IS_VERCEL else "./workspaces"
DEFAULT_CHECKPOINT_DB = str(Path(tempfile.gettempdir()) / ".swe_cache" / "checkpoints.db") if IS_VERCEL else ".swe_cache/checkpoints.db"
DEFAULT_TRACE_DIR = str(Path(tempfile.gettempdir()) / ".swe_cache" / "traces") if IS_VERCEL else ".swe_cache/traces"

try:
    from pydantic_settings import BaseSettings
    from pydantic import Field

    class Settings(BaseSettings):
        llm_provider: str = Field(default="gemini", env="LLM_PROVIDER")
        llm_model: str = Field(default="gemini-1.5-pro", env="LLM_MODEL")
        gemini_api_key: str = Field(default="", env="GEMINI_API_KEY")
        openai_api_key: str = Field(default="", env="OPENAI_API_KEY")

        # Telemetry & Budget Limits
        max_budget_usd: float = Field(default=5.0, env="MAX_BUDGET_USD")
        checkpoint_db_path: str = Field(default=DEFAULT_CHECKPOINT_DB, env="CHECKPOINT_DB_PATH")
        trace_log_dir: str = Field(default=DEFAULT_TRACE_DIR, env="TRACE_LOG_DIR")

        # Execution & Limits
        max_repair_iterations: int = Field(default=3, env="MAX_REPAIR_ITERATIONS")
        sandbox_mode: str = Field(default="local", env="SANDBOX_MODE")
        sandbox_docker_image: str = Field(default="swe-sandbox:latest", env="SANDBOX_DOCKER_IMAGE")
        workspace_base_dir: str = Field(default=DEFAULT_WORKSPACE_BASE, env="WORKSPACE_BASE_DIR")
        auto_approve_deployment: bool = Field(default=False, env="AUTO_APPROVE_DEPLOYMENT")

        # Git & Delivery
        git_author_name: str = Field(default="MAS Delivery Agent", env="GIT_AUTHOR_NAME")
        git_author_email: str = Field(default="mas-agent@autonomous.local", env="GIT_AUTHOR_EMAIL")
        docker_registry: str = Field(default="ghcr.io/org", env="DOCKER_REGISTRY")
        github_default_branch: str = Field(default="main", env="GITHUB_DEFAULT_BRANCH")

        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            extra = "ignore"

    settings = Settings()

except ImportError:
    # Graceful fallback when running in a minimal environment prior to pip install
    class SettingsFallback:
        def __init__(self):
            self.llm_provider = os.getenv("LLM_PROVIDER", "gemini")
            self.llm_model = os.getenv("LLM_MODEL", "gemini-1.5-pro")
            self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
            self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
            self.max_budget_usd = float(os.getenv("MAX_BUDGET_USD", "5.0"))
            self.checkpoint_db_path = os.getenv("CHECKPOINT_DB_PATH", DEFAULT_CHECKPOINT_DB)
            self.trace_log_dir = os.getenv("TRACE_LOG_DIR", DEFAULT_TRACE_DIR)
            self.max_repair_iterations = int(os.getenv("MAX_REPAIR_ITERATIONS", "3"))
            self.sandbox_mode = os.getenv("SANDBOX_MODE", "local")
            self.sandbox_docker_image = os.getenv("SANDBOX_DOCKER_IMAGE", "swe-sandbox:latest")
            self.workspace_base_dir = os.getenv("WORKSPACE_BASE_DIR", DEFAULT_WORKSPACE_BASE)
            self.auto_approve_deployment = os.getenv("AUTO_APPROVE_DEPLOYMENT", "false").lower() == "true"
            self.git_author_name = os.getenv("GIT_AUTHOR_NAME", "MAS Delivery Agent")
            self.git_author_email = os.getenv("GIT_AUTHOR_EMAIL", "mas-agent@autonomous.local")
            self.docker_registry = os.getenv("DOCKER_REGISTRY", "ghcr.io/org")
            self.github_default_branch = os.getenv("GITHUB_DEFAULT_BRANCH", "main")

    settings = SettingsFallback()

def get_workspace_dir(session_id: str) -> Path:
    if os.getenv("VERCEL") == "1" or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        base = Path(tempfile.gettempdir()) / "workspaces"
    else:
        base = Path(settings.workspace_base_dir)
    target = base / session_id
    target.mkdir(parents=True, exist_ok=True)
    return target

def get_checkpoint_db_path() -> Path:
    if os.getenv("VERCEL") == "1" or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        path = Path(tempfile.gettempdir()) / ".swe_cache" / "checkpoints.db"
    else:
        path = Path(settings.checkpoint_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def get_trace_log_dir() -> Path:
    if os.getenv("VERCEL") == "1" or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        path = Path(tempfile.gettempdir()) / ".swe_cache" / "traces"
    else:
        path = Path(settings.trace_log_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path

def get_sandbox_docker_image() -> str:
    return getattr(settings, "sandbox_docker_image", "swe-sandbox:latest")
