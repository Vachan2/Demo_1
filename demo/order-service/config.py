"""
Application configuration via environment variables.
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://orders:orders@localhost:5432/orders"
    db_pool_min: int = 2
    db_pool_max: int = 10

    # App
    app_version: str = "1.0.0"
    app_name: str = "order-api"

    # Failure injection
    # Values: "none" | "connection_leak"
    demo_failure_mode: str = "none"

    # Sentinel reporting
    sentinel_url: str = "http://localhost:8001"
    metrics_push_interval: float = 2.0  # seconds

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
