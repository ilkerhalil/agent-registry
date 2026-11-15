import json
import os

import pytest
from fastapi.testclient import TestClient

from agent_registry.api import app
from agent_registry.config import get_settings
from agent_registry.db import close_pool, init_schema, pool

API_KEYS = {
    "pub-denizbank": {"actor": "ci-bot", "roles": ["publisher"], "tenants": ["denizbank"]},
    "runtime-denizbank": {
        "actor": "runtime-denizbank",
        "roles": ["runtime"],
        "tenants": ["denizbank"],
    },
    "sec-global": {"actor": "security-bot", "roles": ["security"], "tenants": ["*"]},
}

PUBLISHER = {"Authorization": "Bearer pub-denizbank"}
RUNTIME = {"Authorization": "Bearer runtime-denizbank"}
SECURITY = {"Authorization": "Bearer sec-global"}

MANIFEST = {
    "agent": "claim-triage",
    "version": "2.3.1",
    "owner": "hasar-operasyon",
    "tenant": "denizbank",
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

EVAL = {
    "suite": "claim-triage-v4",
    "success_rate": 0.947,
    "total_cases": 300,
    "suites": {
        "regression-core": {"passed": True, "success_rate": 0.94},
        "adversarial": {"passed": True, "success_rate": 0.91},
    },
}


@pytest.fixture(scope="session", autouse=True)
def _auth_env(tmp_path_factory):
    os.environ["REGISTRY_API_KEYS"] = json.dumps(API_KEYS)
    os.environ["ARTIFACT_DIR"] = str(tmp_path_factory.mktemp("artifacts"))
    get_settings.cache_clear()
    yield
    os.environ.pop("REGISTRY_API_KEYS", None)
    os.environ.pop("ARTIFACT_DIR", None)
    get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def _schema(_auth_env):
    init_schema()
    yield
    close_pool()


@pytest.fixture(autouse=True)
def _clean():
    with pool().connection() as conn:
        conn.execute(
            "TRUNCATE audit_event, dependency, promotion, deployment, "
            "agent_version, agent RESTART IDENTITY CASCADE"
        )
        conn.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app, headers=PUBLISHER)


@pytest.fixture
def anon_client():
    return TestClient(app)


@pytest.fixture
def invalid_client():
    return TestClient(app, headers={"Authorization": "Bearer gecersiz-anahtar"})


@pytest.fixture
def runtime_client():
    return TestClient(app, headers=RUNTIME)


@pytest.fixture
def security_client():
    return TestClient(app, headers=SECURITY)
