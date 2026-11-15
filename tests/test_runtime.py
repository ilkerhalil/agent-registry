"""Runtime SDK testleri — imza, hash, süre ve önbellek davranışı."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agent_registry.manifest import AgentManifest
from agent_registry.runtime import AgentRuntime, ResolutionError
from agent_registry.service import build_signed_payload
from agent_registry.signing import sign

KEY = "test-signing-key"
TENANT = "denizbank"

MANIFEST = {
    "agent": "claim-triage",
    "version": "2.3.1",
    "owner": "hasar-operasyon",
    "tenant": TENANT,
    "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
    "model_policy": {
        "primary": "small-classifier",
        "escalate_to": "large-reasoner",
        "budget_usd_per_task": 0.004,
        "max_latency_p95_ms": 2500,
    },
    "tools": ["policy_lookup", "claim_read", "branch_assign"],
    "data_scopes": ["claims:read", "branches:read", "branches:write"],
    "evals": {
        "suite": "claim-triage-v4",
        "min_success_rate": 0.92,
        "must_pass": ["regression-core"],
    },
    "human_gate": "tutar > 50.000 TL ise onay",
    "sla": {"availability": 99.5, "fallback": "insan kuyruğu"},
}


def make_payload(*, manifest: dict | None = None, expires_in: int = 300, key: str = KEY) -> dict:
    manifest = manifest or MANIFEST
    expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
    signed = build_signed_payload(
        agent=manifest["agent"],
        tenant=manifest["tenant"],
        version=manifest["version"],
        env="prod",
        manifest_hash=AgentManifest.model_validate(manifest).sha256(),
        expires_at=expires_at,
    )
    return {
        "agent": manifest["agent"],
        "tenant": manifest["tenant"],
        "version": manifest["version"],
        "env": "prod",
        "manifest": manifest,
        "manifest_hash": AgentManifest.model_validate(manifest).sha256(),
        "signature": sign(signed, key),
        "expires_at": expires_at,
    }


def make_runtime(tmp_path, fetch):
    return AgentRuntime(
        base_url="http://registry.test",
        api_key="runtime-denizbank",
        signing_key=KEY,
        tenant=TENANT,
        cache_dir=tmp_path,
        fetch=fetch,
    )


def test_resolves_and_verifies_signed_manifest(tmp_path):
    runtime = make_runtime(tmp_path, lambda url, key, timeout: make_payload())

    agent = runtime.resolve("claim-triage")

    assert agent.version == "2.3.1"
    assert agent.from_cache is False
    assert agent.manifest_obj.purpose.startswith("Hasar ihbarını")
    assert (tmp_path / "denizbank__claim-triage__prod.json").exists()


def test_rejects_tampered_manifest(tmp_path):
    payload = make_payload()
    payload["manifest"]["data_scopes"] = ["claims:read", "*:*"]  # imzadan sonra değişti

    runtime = make_runtime(tmp_path, lambda url, key, timeout: payload)

    with pytest.raises(ResolutionError, match="hash"):
        runtime.resolve("claim-triage")
    assert not (tmp_path / "denizbank__claim-triage__prod.json").exists()


def test_rejects_wrong_signing_key(tmp_path):
    runtime = make_runtime(tmp_path, lambda url, key, timeout: make_payload(key="baska-anahtar"))

    with pytest.raises(ResolutionError, match="imza"):
        runtime.resolve("claim-triage")


def test_rejects_expired_manifest(tmp_path):
    runtime = make_runtime(tmp_path, lambda url, key, timeout: make_payload(expires_in=-60))

    with pytest.raises(ResolutionError, match="süresi"):
        runtime.resolve("claim-triage")


def test_rejects_other_tenant(tmp_path):
    other = {**MANIFEST, "tenant": "baska-banka"}
    payload = make_payload(manifest=other)

    runtime = make_runtime(tmp_path, lambda url, key, timeout: payload)

    with pytest.raises(ResolutionError):
        runtime.resolve("claim-triage")


def test_falls_back_to_cache_when_registry_is_down(tmp_path):
    runtime = make_runtime(tmp_path, lambda url, key, timeout: make_payload())
    runtime.resolve("claim-triage")

    def offline(url, key, timeout):
        raise ResolutionError("registry erişilemedi: bağlantı yok")

    offline_runtime = make_runtime(tmp_path, offline)
    agent = offline_runtime.resolve("claim-triage")

    assert agent.from_cache is True
    assert agent.version == "2.3.1"


def test_expired_cache_is_not_used(tmp_path):
    runtime = make_runtime(tmp_path, lambda url, key, timeout: make_payload())
    runtime.resolve("claim-triage")

    path = tmp_path / "denizbank__claim-triage__prod.json"
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(cached), encoding="utf-8")

    def offline(url, key, timeout):
        raise ResolutionError("registry erişilemedi: bağlantı yok")

    with pytest.raises(ResolutionError, match="önbellekteki"):
        make_runtime(tmp_path, offline).resolve("claim-triage")


def test_no_cache_and_registry_down_fails(tmp_path):
    def offline(url, key, timeout):
        raise ResolutionError("registry erişilemedi: bağlantı yok")

    with pytest.raises(ResolutionError, match="önbellekte"):
        make_runtime(tmp_path, offline).resolve("claim-triage")
