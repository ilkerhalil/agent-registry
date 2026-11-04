"""İş kuralları: kayıt, yaşam döngüsü, çözümleme."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from . import signing, store
from .config import get_settings
from .manifest import AgentManifest
from .states import TransitionError, check_transition


class RegistryError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "registry_error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# --------------------------------------------------------------------------- kayıt


def register_agent(
    *,
    tenant: str,
    name: str,
    owner_team: str,
    purpose: str,
    actor: str,
    criticality: str = "standard",
    data_classification: str = "internal",
    source_ip: str | None = None,
) -> dict:
    if store.get_agent(tenant, name):
        raise RegistryError(f"{tenant}/{name} zaten kayıtlı", status=409, code="already_exists")
    agent = store.insert_agent(
        name=name,
        tenant=tenant,
        owner_team=owner_team,
        purpose=purpose,
        criticality=criticality,
        data_classification=data_classification,
    )
    store.insert_audit(
        actor=actor,
        action="agent.register",
        object_type="agent",
        object_id=f"{tenant}/{name}",
        after_hash=None,
        detail={"owner_team": owner_team},
        source_ip=source_ip,
    )
    return agent


def create_version(
    *,
    tenant: str,
    name: str,
    manifest: AgentManifest,
    actor: str,
    git_commit: str | None = None,
    source_ip: str | None = None,
) -> dict:
    agent = store.get_agent(tenant, name)
    if not agent:
        raise RegistryError(f"{tenant}/{name} kayıtlı değil", status=404, code="agent_not_found")
    if agent["retired_at"]:
        raise RegistryError("emekli ajanın yeni sürümü açılamaz", status=409, code="agent_retired")
    if manifest.agent != name or manifest.tenant != tenant:
        raise RegistryError(
            "manifest içindeki agent/tenant, istek yoluyla uyuşmuyor",
            status=422,
            code="manifest_mismatch",
        )
    if manifest.owner != agent["owner_team"]:
        raise RegistryError(
            f"manifest sahibi ({manifest.owner}), ajanın sahibiyle uyuşmuyor "
            f"({agent['owner_team']})",
            status=422,
            code="owner_mismatch",
        )
    if store.get_version(agent["id"], manifest.version):
        raise RegistryError(
            f"{name}@{manifest.version} zaten kayıtlı", status=409, code="version_exists"
        )
    manifest_json = json.loads(manifest.canonical_json())
    manifest_hash = manifest.sha256()

    version = store.insert_version(
        agent_id=agent["id"],
        semver=manifest.version,
        manifest_json=manifest_json,
        manifest_hash=manifest_hash,
        git_commit=git_commit,
        eval_suite=manifest.evals.suite,
        created_by=actor,
    )
    deps: list[tuple[str, str]] = []
    deps += [("tool", t) for t in manifest.tools]
    deps += [
        ("model", m)
        for m in filter(None, [manifest.model_policy.primary, manifest.model_policy.escalate_to])
    ]
    store.insert_dependencies(version["id"], deps)
    store.insert_audit(
        actor=actor,
        action="version.create",
        object_type="version",
        object_id=f"{tenant}/{name}@{manifest.version}",
        after_hash=manifest_hash,
        detail={"git_commit": git_commit},
        source_ip=source_ip,
    )
    return version


# --------------------------------------------------------------------- doğrulama


def validate_version(
    *,
    tenant: str,
    name: str,
    semver: str,
    eval_result: dict,
    actor: str,
    source_ip: str | None = None,
) -> dict:
    version = _require_version(tenant, name, semver)
    manifest = AgentManifest.model_validate(version["manifest_json"])
    _check_eval_gates(manifest, eval_result)
    try:
        check_transition(version["state"], "validated")
    except TransitionError as exc:
        raise RegistryError(str(exc), status=409, code="invalid_transition") from exc
    store.set_eval_result(version["id"], eval_result, eval_result["suite"])
    store.set_state(version["id"], "validated")
    store.insert_audit(
        actor=actor,
        action="version.validate",
        object_type="version",
        object_id=f"{tenant}/{name}@{semver}",
        after_hash=version["manifest_hash"],
        detail={"success_rate": eval_result["success_rate"]},
        source_ip=source_ip,
    )
    return {"semver": semver, "state": "validated", "success_rate": eval_result["success_rate"]}


def _check_eval_gates(manifest: AgentManifest, eval_result: dict) -> None:
    suite = eval_result.get("suite")
    if suite != manifest.evals.suite:
        raise RegistryError(
            f"eval suite uyuşmuyor: beklenen {manifest.evals.suite}, gelen {suite}",
            status=422,
            code="eval_suite_mismatch",
        )
    rate = float(eval_result.get("success_rate", 0.0))
    if rate < manifest.evals.min_success_rate:
        raise RegistryError(
            f"eval eşiği sağlanmadı: {rate:.3f} < {manifest.evals.min_success_rate:.3f}",
            status=422,
            code="eval_threshold",
        )
    suites = eval_result.get("suites") or {}
    for required in manifest.evals.must_pass:
        outcome = suites.get(required)
        if not outcome:
            raise RegistryError(
                f"zorunlu suite eksik: {required}", status=422, code="eval_missing_suite"
            )
        if not outcome.get("passed"):
            raise RegistryError(
                f"zorunlu suite geçmedi: {required}", status=422, code="eval_regression"
            )


# ------------------------------------------------------------------- yaşam döngüsü


def promote_version(
    *,
    tenant: str,
    name: str,
    semver: str,
    actor: str,
    env: str = "prod",
    gate_results: dict | None = None,
    source_ip: str | None = None,
) -> dict:
    version = _require_version(tenant, name, semver)
    agent = store.get_agent(tenant, name)
    if agent and agent["retired_at"]:
        raise RegistryError("emekli ajan terfi ettirilemez", status=409, code="agent_retired")
    if version["state"] not in ("validated", "candidate"):
        raise RegistryError(
            f"terfi için validated durumu gerekli (şu an: {version['state']})",
            status=409,
            code="not_validated",
        )
    check_transition(version["state"], "active")

    previous = store.get_active_version(version["agent_id"])
    if previous:
        check_transition(previous["state"], "deprecated")
        store.set_state(previous["id"], "deprecated")

    store.set_state(version["id"], "active")
    record = store.insert_promotion(
        from_version=previous["id"] if previous else None,
        to_version=version["id"],
        gate_results=gate_results or {"source": "ci"},
        approver=actor,
        decision="approved",
    )
    deployment = store.insert_deployment(
        version_id=version["id"],
        env=env,
        deployed_by=actor,
        config_hash=version["manifest_hash"],
    )
    store.insert_audit(
        actor=actor,
        action="version.promote",
        object_type="version",
        object_id=f"{tenant}/{name}@{semver}",
        before_hash=previous["manifest_hash"] if previous else None,
        after_hash=version["manifest_hash"],
        detail={"env": env, "replaced": previous["semver"] if previous else None},
        source_ip=source_ip,
    )
    return {
        "semver": semver,
        "state": "active",
        "env": env,
        "promotion_id": record["id"],
        "replaced": previous["semver"] if previous else None,
        "deployment_id": deployment["id"],
    }


def transition(
    *, tenant: str, name: str, semver: str, target: str, actor: str, source_ip: str | None = None
) -> dict:
    version = _require_version(tenant, name, semver)
    try:
        check_transition(version["state"], target)
    except TransitionError as exc:
        raise RegistryError(str(exc), status=409, code="invalid_transition") from exc
    store.set_state(version["id"], target)
    store.insert_audit(
        actor=actor,
        action=f"version.{target}",
        object_type="version",
        object_id=f"{tenant}/{name}@{semver}",
        after_hash=version["manifest_hash"],
        detail={"from": version["state"], "to": target},
        source_ip=source_ip,
    )
    return {"semver": semver, "state": target, "previous_state": version["state"]}


# ---------------------------------------------------------------------- çözümleme


def resolve(*, tenant: str, name: str, env: str = "prod") -> dict:
    agent = store.get_agent(tenant, name)
    if not agent:
        raise RegistryError(f"{tenant}/{name} kayıtlı değil", status=404, code="agent_not_found")
    version = store.get_active_version(agent["id"])
    if not version:
        raise RegistryError(
            f"{tenant}/{name} için aktif sürüm yok", status=404, code="no_active_version"
        )

    expires_at = datetime.now(UTC) + timedelta(seconds=get_settings().resolve_ttl_seconds)
    signed_payload = build_signed_payload(
        agent=name,
        tenant=tenant,
        version=version["semver"],
        manifest_hash=version["manifest_hash"],
        expires_at=expires_at.isoformat(),
    )
    signature = signing.sign(signed_payload, get_settings().registry_signing_key)

    return {
        "agent": name,
        "tenant": tenant,
        "version": version["semver"],
        "env": env,
        "manifest": version["manifest_json"],
        "manifest_hash": version["manifest_hash"],
        "signature": signature,
        "expires_at": expires_at.isoformat(),
    }


# ------------------------------------------------------------------------- yardımcı


def build_signed_payload(
    *, agent: str, tenant: str, version: str, manifest_hash: str, expires_at: str
) -> str:
    """resolve yanıtının imzalanan gövdesi — runtime da aynı biçimi doğrular."""
    return json.dumps(
        {
            "agent": agent,
            "tenant": tenant,
            "version": version,
            "manifest_hash": manifest_hash,
            "expires_at": expires_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _require_version(tenant: str, name: str, semver: str) -> dict:
    agent = store.get_agent(tenant, name)
    if not agent:
        raise RegistryError(f"{tenant}/{name} kayıtlı değil", status=404, code="agent_not_found")
    version = store.get_version(agent["id"], semver)
    if not version:
        raise RegistryError(f"sürüm bulunamadı: {semver}", status=404, code="version_not_found")
    return version


def inventory(tenant: str | None = None, owner: str | None = None) -> list[dict[str, Any]]:
    return store.list_agents(tenant=tenant, owner=owner)
