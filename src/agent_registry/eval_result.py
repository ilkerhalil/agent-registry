"""Eval sonucunun kanıt zorunluluğu.

Tek başına bir `success_rate` sayısı kanıt değildir: elle yazılabilir. Bu modül
sayının yanında neyin ölçüldüğünü de ister — kaç vaka, hangi veri kümesi, hangi
harness sürümü, ne zaman koştu. Kural: kanıt alanları eksikse sonuç geçersizdir.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

# sha256:<hex> ya da çıplak hex; kısa özetlere izin verilmez (en az 16 karakter).
DATASET_HASH = re.compile(r"^(sha256:)?[0-9a-f]{16,64}$")


class EvalEvidenceError(Exception):
    """Eval sonucu kanıt olarak yetersiz."""


class SuiteOutcome(BaseModel):
    passed: bool
    success_rate: float = Field(ge=0.0, le=1.0)
    total_cases: int = Field(gt=0)

    model_config = {"extra": "allow"}


class EvalResult(BaseModel):
    """Kanıt alanları zorunlu; ek alanlar serbest bırakıldı."""

    suite: str = Field(min_length=1, max_length=120)
    success_rate: float = Field(ge=0.0, le=1.0)
    total_cases: int = Field(gt=0)
    dataset_hash: str = Field(min_length=16, max_length=71)
    harness_version: str = Field(min_length=1, max_length=120)
    started_at: datetime
    finished_at: datetime
    suites: dict[str, SuiteOutcome] = Field(default_factory=dict)
    attestation: dict[str, Any] | None = None

    model_config = {"extra": "allow"}

    @model_validator(mode="after")
    def _evidence_is_consistent(self) -> EvalResult:
        if not DATASET_HASH.match(self.dataset_hash):
            raise ValueError("dataset_hash must be a hex digest, optionally prefixed with sha256:")

        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("started_at and finished_at must carry a timezone")

        if self.finished_at <= self.started_at:
            raise ValueError("finished_at must be after started_at")

        if self.suites:
            counted = sum(s.total_cases for s in self.suites.values())
            if counted != self.total_cases:
                raise ValueError(
                    f"suite case counts add up to {counted} but total_cases says {self.total_cases}"
                )

        return self


def parse_eval_result(payload: dict[str, Any]) -> EvalResult:
    """Doğrular; hata mesajını ilk alanla sınırlar ki CI çıktısı okunur kalsın."""
    try:
        return EvalResult.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors()
        missing = sorted({str(e["loc"][0]) for e in errors if e["type"] == "missing"})
        if missing:
            raise EvalEvidenceError("missing fields: " + ", ".join(missing)) from exc
        first = errors[0]
        location = ".".join(str(part) for part in first["loc"]) or "eval_result"
        raise EvalEvidenceError(f"{location}: {first['msg']}") from exc
