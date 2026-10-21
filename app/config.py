"""Application settings loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    llm_provider: Literal["mock", "anthropic"] = "mock"
    llm_model: str = "claude-sonnet-5-5"
    # Used for cheap, simple calls such as extracting event details.
    llm_cheap_model: str = "claude-haiku-5-5"
    app_env: Literal["dev", "prod"] = "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
