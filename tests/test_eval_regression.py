"""M1: eval artefaktı + regresyon karşılaştırması."""

from __future__ import annotations

import json

from agent_registry.artifacts import FileSystemStore, artifact_key, canonical_bytes
from agent_registry.config import get_settings

from .conftest import EVAL, MANIFEST

BASE = "/v1/agents/denizbank/claim-triage"


def _register(client):
    return client.post(
        "/v1/agents",
        json={
            "name": "claim-triage",
            "tenant": "denizbank",
            "owner_team": "hasar-operasyon",
            "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
        },
    )


def _version(client, semver):
    return client.post(f"{BASE}/versions", json={"manifest": dict(MANIFEST, version=semver)})


def _validate(client, semver, evals):
    return client.post(f"{BASE}/versions/{semver}:validate", json=evals)


def _promote(client, semver):
    return client.post(f"{BASE}/versions/{semver}:promote", json={"env": "prod"})


def _eval(rate: float) -> dict:
    return {**EVAL, "success_rate": rate}


def test_eval_artifact_is_stored_and_readable(client):
    assert _register(client).status_code == 201
    assert _version(client, "2.3.1").status_code == 201
    assert _validate(client, "2.3.1", EVAL).status_code == 200

    expected_key = artifact_key(canonical_bytes(EVAL))
    store = FileSystemStore(get_settings().artifact_dir)

    assert store.exists(expected_key)
    assert json.loads(store.get(expected_key)) == EVAL


def test_regression_against_active_version_is_rejected(client):
    assert _register(client).status_code == 201
    assert _version(client, "2.3.1").status_code == 201
    assert _validate(client, "2.3.1", _eval(0.95)).status_code == 200
    assert _promote(client, "2.3.1").status_code == 200

    assert _version(client, "2.3.2").status_code == 201
    # 0.921 eşiği (0.92) geçer ama aktif sürümün 0.95'inden 2,9 puan düşük
    r = _validate(client, "2.3.2", _eval(0.921))

    assert r.status_code == 422
    assert r.json()["error"] == "eval_regression"
    assert "2.3.1" in r.json()["message"]


def test_small_drop_within_tolerance_is_accepted(client):
    assert _register(client).status_code == 201
    assert _version(client, "2.3.1").status_code == 201
    assert _validate(client, "2.3.1", _eval(0.95)).status_code == 200
    assert _promote(client, "2.3.1").status_code == 200

    assert _version(client, "2.3.2").status_code == 201
    r = _validate(client, "2.3.2", _eval(0.94))

    assert r.status_code == 200
    assert r.json()["state"] == "validated"


def test_improvement_is_accepted(client):
    assert _register(client).status_code == 201
    assert _version(client, "2.3.1").status_code == 201
    assert _validate(client, "2.3.1", _eval(0.93)).status_code == 200
    assert _promote(client, "2.3.1").status_code == 200

    assert _version(client, "2.3.2").status_code == 201
    assert _validate(client, "2.3.2", _eval(0.97)).status_code == 200
