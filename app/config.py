"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, sourced from environment variables and a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    certificate_output_dir: Path = Path("generated_certificates")


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance so config is read only once per process."""
    return Settings()
