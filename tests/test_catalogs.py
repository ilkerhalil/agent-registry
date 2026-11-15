"""M2: kataloglar registry'de + etki analizi."""

from __future__ import annotations

from .conftest import EVAL, MANIFEST

BASE = "/v1/agents/denizbank/claim-triage"


def _seed_catalogs(client):
    assert (
        client.put(
            "/v1/catalogs/teams/hasar-operasyon",
            json={
                "on_call": "hasar-nobet",
                "allowed_scopes": ["claims:read", "branches:read", "branches:write"],
            },
        ).status_code
        == 200
    )
    for tool, owner in (
        ("policy_lookup", "platform"),
        ("claim_read", "hasar-operasyon"),
        ("branch_assign", "hasar-operasyon"),
    ):
        assert client.put(f"/v1/catalogs/tools/{tool}", json={"owner": owner}).status_code == 200
    assert (
        client.put(
            "/v1/catalogs/models/small-classifier", json={"provider": "internal"}
        ).status_code
        == 200
    )
    assert (
        client.put("/v1/catalogs/models/large-reasoner", json={"provider": "cloud"}).status_code
        == 200
    )


def _register_and_promote(client, semver: str = "2.3.1"):
    client.post(
        "/v1/agents",
        json={
            "name": "claim-triage",
            "tenant": "denizbank",
            "owner_team": "hasar-operasyon",
            "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
        },
    )
    client.post(f"{BASE}/versions", json={"manifest": dict(MANIFEST, version=semver)})
    assert client.post(f"{BASE}/versions/{semver}:validate", json=EVAL).status_code == 200
    return client.post(f"{BASE}/versions/{semver}:promote", json={"env": "prod"})


def test_catalogs_live_in_registry_and_drive_the_gate(client):
    _seed_catalogs(client)

    r = client.get("/v1/catalogs/tools")
    assert r.status_code == 200
    assert {t["name"] for t in r.json()["tools"]} == {
        "policy_lookup",
        "claim_read",
        "branch_assign",
    }

    # Bu ajanın araçları artık YAML'den değil registry'deki katalogdan geliyor.
    assert _register_and_promote(client).status_code == 200


def test_gate_rejects_tool_missing_from_registry_catalog(client):
    _seed_catalogs(client)
    assert _register_and_promote(client).status_code == 200

    extra = dict(MANIFEST, version="2.3.2", tools=["policy_lookup", "bilinmeyen_arac"])
    client.post(f"{BASE}/versions", json={"manifest": extra})
    assert client.post(f"{BASE}/versions/2.3.2:validate", json=EVAL).status_code == 200

    r = client.post(f"{BASE}/versions/2.3.2:promote", json={"env": "prod"})

    assert r.status_code == 409
    detail = r.json()["message"]
    assert "bilinmeyen_arac" in detail


def test_impact_analysis_lists_agents_using_a_tool(client):
    _seed_catalogs(client)
    assert _register_and_promote(client).status_code == 200

    r = client.get("/v1/catalogs/tools/policy_lookup/impact")

    assert r.status_code == 200
    agents = r.json()["agents"]
    assert len(agents) == 1
    assert agents[0]["name"] == "claim-triage"
    assert agents[0]["semver"] == "2.3.1"


def test_impact_analysis_for_model(client):
    _seed_catalogs(client)
    assert _register_and_promote(client).status_code == 200

    r = client.get("/v1/catalogs/models/large-reasoner/impact")

    assert r.status_code == 200
    assert [a["name"] for a in r.json()["agents"]] == ["claim-triage"]


def test_unknown_catalog_kind_is_404(client):
    r = client.get("/v1/catalogs/nonsense/x/impact")

    assert r.status_code == 404
    assert r.json()["error"] == "unknown_catalog"


def test_runtime_role_cannot_write_catalogs(runtime_client):
    r = runtime_client.put("/v1/catalogs/tools/x", json={"owner": "platform"})

    assert r.status_code == 403
