from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["runtime", "publisher", "security"]


class ApiKey(BaseModel):
    """Bir API anahtarının kimliği, rolleri ve erişebildiği kiracılar."""

    actor: str = Field(min_length=1)
    roles: list[Role] = Field(min_length=1)
    tenants: list[str] = Field(min_length=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://registry:registry@127.0.0.1:5433/agentregistry"
    registry_signing_key: str = "dev-only-change-me"
    resolve_ttl_seconds: int = 300
    catalog_dir: str = "./catalogs"
    artifact_dir: str = "./artifacts"
    # Yeni sürümün eval başarı oranı, aktif sürümün altına bu kadar düşemez.
    eval_max_regression: float = 0.02
    api_keys: dict[str, ApiKey] = Field(default_factory=dict, validation_alias="REGISTRY_API_KEYS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
