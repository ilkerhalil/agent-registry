"""M4: Prometheus metrikleri."""

from __future__ import annotations

from .conftest import EVAL, MANIFEST

BASE = "/v1/agents/denizbank/claim-triage"


def _lifecycle(client):
    client.post(
        "/v1/agents",
        json={
            "name": "claim-triage",
            "tenant": "denizbank",
            "owner_team": "hasar-operasyon",
            "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
        },
    )
    client.post(f"{BASE}/versions", json={"manifest": MANIFEST})
    client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL)
    client.post(f"{BASE}/versions/2.3.1:promote", json={"env": "prod"})


def test_metrics_requires_security_role(client, runtime_client, anon_client):
    assert anon_client.get("/metrics").status_code == 401
    assert runtime_client.get("/metrics").status_code == 403


def test_metrics_exposes_lifecycle_counts(client, security_client):
    _lifecycle(client)

    r = security_client.get("/metrics")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert 'agent_registry_versions_total{state="active",tenant="denizbank"} 1' in body
    assert 'agent_registry_deployments_total{env="prod",tenant="denizbank"} 1' in body
    assert 'agent_registry_agents_total{tenant="denizbank"} 1' in body
    assert "agent_registry_blocked_versions_total" in body
    assert "# TYPE agent_registry_versions_total gauge" in body
