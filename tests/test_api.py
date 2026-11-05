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


def test_promote_runs_gate_server_side(client, security_client):
    _register(client)
    client.post(f"{BASE}/versions", json={"manifest": dict(MANIFEST, tools=["ghost_tool"])})
    assert client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL).status_code == 200

    r = client.post(f"{BASE}/versions/2.3.1:promote", json={})
    assert r.status_code == 409
    assert r.json()["error"] == "gate_failed"

    # sürüm aktif olmadı ve reddedilen terfi denetim izine yazıldı
    assert client.get(f"{BASE}/resolve").status_code == 404
    events = security_client.get("/v1/audit", params={"object_type": "version"}).json()["events"]
    assert any(e["action"] == "version.promote_rejected" for e in events)


def test_promote_rolls_back_on_failure(client, monkeypatch):
    _register(client)
    _create_version(client, "2.3.1")
    client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL)
    assert client.post(f"{BASE}/versions/2.3.1:promote", json={}).status_code == 200

    _create_version(client, "2.4.0")
    client.post(f"{BASE}/versions/2.4.0:validate", json=EVAL)

    def boom(**_kwargs):
        raise RuntimeError("deployment yazılamadı")

    monkeypatch.setattr("agent_registry.store.insert_deployment", boom)
    with pytest.raises(RuntimeError):
        client.post(f"{BASE}/versions/2.4.0:promote", json={})

    # yarım kalan terfi geri alındı: önceki sürüm aktif kaldı
    assert client.get(f"{BASE}/resolve").json()["version"] == "2.3.1"


def test_block_requires_security_role(client, security_client):
    _register(client)
    _create_version(client)
    r = client.post(f"{BASE}/versions/2.3.1:block")
    assert r.status_code == 403
    r = security_client.post(f"{BASE}/versions/2.3.1:block")
    assert r.status_code == 200
    assert r.json()["state"] == "blocked"


def test_missing_token_rejected(anon_client):
    r = anon_client.get(f"{BASE}/resolve")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"
    assert r.headers["www-authenticate"] == "Bearer"


def test_invalid_token_rejected(invalid_client):
    r = invalid_client.get(f"{BASE}/resolve")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_publisher_cannot_reach_other_tenant(client):
    r = client.post(
        "/v1/agents",
        json={
            "name": "other-agent",
            "tenant": "intertech",
            "owner_team": "platform",
            "purpose": "Baska kiracida kayitli bir ajan",
        },
    )
    assert r.status_code == 403
    assert r.json()["error"] == "forbidden_tenant"
    assert client.get("/v1/agents/intertech/other-agent/resolve").status_code == 403


def test_list_agents_is_tenant_scoped(client):
    _register(client)
    r = client.get("/v1/agents")
    assert r.status_code == 200
    assert all(a["tenant"] == "denizbank" for a in r.json()["agents"])
    assert client.get("/v1/agents", params={"tenant": "intertech"}).status_code == 403


def test_runtime_can_resolve_but_not_promote(client, runtime_client):
    _register(client)
    _create_version(client)
    client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL)
    assert client.post(f"{BASE}/versions/2.3.1:promote", json={}).status_code == 200

    assert runtime_client.get(f"{BASE}/resolve").status_code == 200
    assert runtime_client.post(f"{BASE}/versions/2.3.1:promote", json={}).status_code == 403


def test_audit_requires_security_role(client, security_client):
    assert client.get("/v1/audit").status_code == 403
    assert security_client.get("/v1/audit").status_code == 200


def test_audit_trail_written(client, security_client):
    _register(client)
    r = security_client.get("/v1/audit", params={"object_type": "agent"})
    events = r.json()["events"]
    assert any(e["action"] == "agent.register" for e in events)


def test_audit_is_append_only(client):
    _register(client)
    with pytest.raises(psycopg.errors.RaiseException):
        with pool().connection() as conn:
            conn.execute("UPDATE audit_event SET actor = 'attacker'")
            conn.commit()
