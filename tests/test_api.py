import psycopg
import pytest

from agent_registry.config import get_settings
from agent_registry.db import pool
from agent_registry.service import build_signed_payload
from agent_registry.signing import verify

from .conftest import EVAL, MANIFEST

BASE = "/v1/agents/denizbank/claim-triage"


def _register(client, name="claim-triage", tenant="denizbank"):
    return client.post(
        "/v1/agents",
        json={
            "name": name,
            "tenant": tenant,
            "owner_team": "hasar-operasyon",
            "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
        },
    )


def _create_version(client, version="2.3.1"):
    return client.post(f"{BASE}/versions", json={"manifest": dict(MANIFEST, version=version)})


def test_full_lifecycle_and_signed_resolve(client):
    assert _register(client).status_code == 201
    assert _create_version(client).status_code == 201

    # aktif sürüm yokken resolve 404
    r = client.get(f"{BASE}/resolve")
    assert r.status_code == 404
    assert r.json()["error"] == "no_active_version"

    # eval eşiğini geçen doğrulama
    r = client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL)
    assert r.status_code == 200
    assert r.json()["state"] == "validated"

    # terfi
    r = client.post(f"{BASE}/versions/2.3.1:promote", json={"env": "prod"})
    assert r.status_code == 200
    assert r.json()["state"] == "active"
    assert r.json()["replaced"] is None

    # imzalı resolve
    r = client.get(f"{BASE}/resolve")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "2.3.1"
    payload = build_signed_payload(
        agent=body["agent"],
        tenant=body["tenant"],
        version=body["version"],
        manifest_hash=body["manifest_hash"],
        expires_at=body["expires_at"],
    )
    verify(payload, body["signature"], get_settings().registry_signing_key)


def test_promote_requires_validation(client):
    _register(client)
    _create_version(client)
    r = client.post(f"{BASE}/versions/2.3.1:promote", json={})
    assert r.status_code == 409
    assert r.json()["error"] == "not_validated"


def test_low_eval_blocks_validation(client):
    _register(client)
    _create_version(client)
    r = client.post(f"{BASE}/versions/2.3.1:validate", json=dict(EVAL, success_rate=0.5))
    assert r.status_code == 422
    assert r.json()["error"] == "eval_threshold"


def test_second_version_replaces_first(client):
    _register(client)
    _create_version(client, "2.3.1")
    client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL)
    client.post(f"{BASE}/versions/2.3.1:promote", json={})

    _create_version(client, "2.4.0")
    client.post(f"{BASE}/versions/2.4.0:validate", json=EVAL)
    r = client.post(f"{BASE}/versions/2.4.0:promote", json={})
    assert r.json()["replaced"] == "2.3.1"

    assert client.get(f"{BASE}/resolve").json()["version"] == "2.4.0"


def test_manifest_tenant_mismatch_rejected(client):
    _register(client)
    r = client.post(f"{BASE}/versions", json={"manifest": dict(MANIFEST, tenant="intertech")})
    assert r.status_code == 422
    assert r.json()["error"] == "manifest_mismatch"


def test_duplicate_version_rejected(client):
    _register(client)
    assert _create_version(client).status_code == 201
    r = _create_version(client)
    assert r.status_code >= 400


def test_revalidating_version_returns_conflict(client):
    _register(client)
    _create_version(client)
    assert client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL).status_code == 200
    r = client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL)
    assert r.status_code == 409
    assert r.json()["error"] == "invalid_transition"


def test_manifest_owner_must_match_registered_team(client):
    _register(client)
    r = client.post(f"{BASE}/versions", json={"manifest": dict(MANIFEST, owner="platform")})
    assert r.status_code == 422
    assert r.json()["error"] == "owner_mismatch"


def test_block_requires_security_role(client):
    _register(client)
    _create_version(client)
    r = client.post(f"{BASE}/versions/2.3.1:block")
    assert r.status_code == 403
    r = client.post(f"{BASE}/versions/2.3.1:block", headers={"X-Role": "security"})
    assert r.status_code == 200
    assert r.json()["state"] == "blocked"


def test_audit_trail_written(client):
    _register(client)
    r = client.get("/v1/audit", params={"object_type": "agent"})
    events = r.json()["events"]
    assert any(e["action"] == "agent.register" for e in events)


def test_audit_is_append_only(client):
    _register(client)
    with pytest.raises(psycopg.errors.RaiseException):
        with pool().connection() as conn:
            conn.execute("UPDATE audit_event SET actor = 'attacker'")
            conn.commit()
