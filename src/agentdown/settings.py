"""Runtime configuration, read from AGENTDOWN_* environment variables (and .env locally)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTDOWN_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/agentdown.db"
    catalog_path: str = "catalog/services.yaml"
    # Base URL agents are told to use (report URL, methodology link). No trailing slash.
    public_url: str = "http://127.0.0.1:8000"
    host: str = "127.0.0.1"
    port: int = 8000
    # Which proxies may set X-Forwarded-For. On the VM the app port is reachable only
    # from Caddy, so production sets "*". The client IP is used in memory only.
    forwarded_allow_ips: str = "127.0.0.1"
    # Run the 5-minute detector and salt pruning in the background. Tests turn it off.
    run_jobs: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
