from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://registry:registry@127.0.0.1:5433/agentregistry"
    registry_signing_key: str = "dev-only-change-me"
    resolve_ttl_seconds: int = 300
    catalog_dir: str = "./catalogs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
