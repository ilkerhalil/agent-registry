"""Ajan manifesti: şema, doğrulama ve değişmez hash."""

import hashlib
import json
import re

from pydantic import BaseModel, Field, field_validator

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class ModelPolicy(BaseModel):
    primary: str = Field(min_length=1)
    escalate_to: str | None = None
    budget_usd_per_task: float = Field(gt=0)
    max_latency_p95_ms: int = Field(gt=0)


class EvalSpec(BaseModel):
    suite: str = Field(min_length=1)
    min_success_rate: float = Field(ge=0.0, le=1.0)
    must_pass: list[str] = Field(default_factory=list)


class SLASpec(BaseModel):
    availability: float = Field(ge=0.0, le=100.0)
    fallback: str = Field(min_length=1)


class AgentManifest(BaseModel):
    """Bir ajan sürümünün sözleşmesi. Değişmez: yeni sürüm = yeni manifest."""

    agent: str
    version: str
    owner: str = Field(min_length=1)
    tenant: str = Field(min_length=1)
    purpose: str = Field(min_length=10)
    model_policy: ModelPolicy
    tools: list[str] = Field(default_factory=list)
    data_scopes: list[str] = Field(default_factory=list)
    evals: EvalSpec
    human_gate: str | None = None
    sla: SLASpec
    retirement: str | None = None

    @field_validator("agent")
    @classmethod
    def _agent_name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("agent adı ^[a-z][a-z0-9-]{2,62}$ kalıbına uymalı")
        return v

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError("version semver olmalı (örn. 2.3.1)")
        return v

    @field_validator("tools", "data_scopes")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("tekrarlanan kayıt var")
        return v

    def canonical_json(self) -> str:
        """İmza ve hash için kanonik gösterim."""
        payload = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
