"""Sentinel service configuration."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Thresholds for incident detection
    error_rate_threshold_pct: float = 5.0
    latency_p95_threshold_ms: float = 2000.0
    db_pool_utilisation_threshold_pct: float = 80.0

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # GitHub
    github_token: str = ""
    github_repo: str = ""          # e.g. "org/order-service"
    github_base_branch: str = "main"

    # Paths for local git repo (used in demo when not using GitHub API)
    local_repo_path: str = "../demo/order-service"

    # Order-API base URL
    order_api_url: str = "http://localhost:9000"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
