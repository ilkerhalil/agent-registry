"""CI kapısı — yedi kontrol. DB'den bağımsız, test edilebilir."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .manifest import AgentManifest


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def load_catalogs(directory: str | Path) -> dict[str, Any]:
    directory = Path(directory)
    out: dict[str, Any] = {}
    for key, filename in (
        ("tools", "tools.yaml"),
        ("models", "models.yaml"),
        ("teams", "teams.yaml"),
    ):
        path = directory / filename
        if path.exists():
            out[key] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            out[key] = {}
    return out


def _tool_names(catalogs: dict) -> set[str]:
    return {t["name"] for t in (catalogs.get("tools") or {}).get("tools", [])}


def _model_names(catalogs: dict) -> set[str]:
    return {m["name"] for m in (catalogs.get("models") or {}).get("models", [])}


def _teams(catalogs: dict) -> dict:
    return (catalogs.get("teams") or {}).get("teams", {})


def run_gate(
    manifest: dict | AgentManifest,
    eval_result: dict | None,
    catalogs: dict,
    conflicts: list[dict] | None = None,
    baseline: dict | None = None,
    max_regression: float = 0.02,
) -> list[Check]:
    checks: list[Check] = []

    # 1 — şema
    try:
        m = (
            manifest
            if isinstance(manifest, AgentManifest)
            else AgentManifest.model_validate(manifest)
        )
        checks.append(Check("manifest_schema", True, f"{m.agent}@{m.version}"))
    except ValidationError as exc:
        first = exc.errors()[0]
        checks.append(Check("manifest_schema", False, f"{first['loc']}: {first['msg']}"))
        return checks  # şema yoksa gerisi anlamsız

    # 2 — eval eşikleri
    if not eval_result:
        checks.append(Check("eval", False, "eval sonucu verilmedi"))
    else:
        problems = []
        if eval_result.get("suite") != m.evals.suite:
            problems.append(f"suite uyuşmuyor ({eval_result.get('suite')} != {m.evals.suite})")
        rate = float(eval_result.get("success_rate", 0) or 0)
        if rate < m.evals.min_success_rate:
            problems.append(f"success_rate {rate:.3f} < {m.evals.min_success_rate:.3f}")
        suites = eval_result.get("suites") or {}
        for required in m.evals.must_pass:
            if not (suites.get(required) or {}).get("passed"):
                problems.append(f"zorunlu suite geçmedi: {required}")
        if baseline and baseline.get("success_rate") is not None:
            previous = float(baseline["success_rate"])
            if rate < previous - max_regression:
                problems.append(
                    f"regresyon: {rate:.3f} < {previous:.3f} - {max_regression:.3f} "
                    f"(aktif sürüm {baseline.get('version', '?')})"
                )
        checks.append(
            Check(
                "eval",
                not problems,
                "; ".join(problems) or f"{rate:.3f} ≥ {m.evals.min_success_rate:.3f}",
            )
        )

    # 3 — veri kapsamı ⊆ ekip izinleri
    team = _teams(catalogs).get(m.owner)
    if not team:
        checks.append(Check("data_scopes", False, f"ekip tanımsız: {m.owner}"))
        checks.append(Check("team", False, f"ekip tanımsız: {m.owner}"))
    else:
        allowed = set(team.get("allowed_scopes") or [])
        extra = sorted(set(m.data_scopes) - allowed)
        checks.append(
            Check(
                "data_scopes", not extra, f"izin dışı: {extra}" if extra else "tüm kapsamlar izinli"
            )
        )
        on_call = team.get("on_call")
        checks.append(
            Check("team", bool(on_call), f"on-call: {on_call}" if on_call else "on-call tanımsız")
        )

    # 4 — araçlar katalogda
    known_tools = _tool_names(catalogs)
    missing_tools = sorted(set(m.tools) - known_tools)
    checks.append(
        Check(
            "tools",
            not missing_tools,
            f"eksik: {missing_tools}" if missing_tools else "tüm araçlar kayıtlı",
        )
    )

    # 5 — modeller katalogda
    known_models = _model_names(catalogs)
    used_models = {x for x in (m.model_policy.primary, m.model_policy.escalate_to) if x}
    missing_models = sorted(used_models - known_models)
    checks.append(
        Check(
            "models",
            not missing_models,
            f"eksik: {missing_models}" if missing_models else "tüm modeller kayıtlı",
        )
    )

    # 7 — kapsam çakışması (başka ekipte aktif aynı kapsam)
    conflicts = conflicts or []
    clashes = [
        f"{c['name']} ({c['owner_team']})"
        for c in conflicts
        if c.get("owner_team") != m.owner and set(c.get("scopes") or []) & set(m.data_scopes)
    ]
    checks.append(
        Check("scope_conflict", not clashes, f"çakışma: {clashes}" if clashes else "çakışma yok")
    )

    return checks


def gate_passed(checks: list[Check]) -> bool:
    return all(c.passed for c in checks)
