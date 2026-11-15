"""B: eval sonucu imzalı gelir — sayıyı ancak anahtarı olan taraf üretebilir."""

from __future__ import annotations

import os

import pytest

from agent_registry.config import get_settings
from agent_registry.eval_attestation import (
    EvalAttestationError,
    build_attestation,
    verify_attestation,
)
from agent_registry.gate import load_catalogs, run_gate
from agent_registry.signers import HmacSigner

from .conftest import EVAL, MANIFEST

CATALOGS = load_catalogs("catalogs")
BASE = "/v1/agents/denizbank/claim-triage"


def _signer() -> HmacSigner:
    return HmacSigner(get_settings().registry_signing_key)


def _signed(evals: dict | None = None) -> dict:
    payload = dict(evals or EVAL)
    payload["attestation"] = build_attestation(payload, _signer())
    return payload


def _checks(evals: dict, *, required: bool):
    return {
        c.name: c
        for c in run_gate(
            MANIFEST,
            evals,
            CATALOGS,
            conflicts=[],
            signer=_signer(),
            require_attestation=required,
        )
    }


def _register_and_version(client):
    assert (
        client.post(
            "/v1/agents",
            json={
                "name": "claim-triage",
                "tenant": "denizbank",
                "owner_team": "hasar-operasyon",
                "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
            },
        ).status_code
        == 201
    )
    assert client.post(f"{BASE}/versions", json={"manifest": MANIFEST}).status_code == 201


def test_signed_payload_verifies():
    payload = _signed()

    verify_attestation(payload, _signer(), required=True)


def test_signature_covers_the_payload():
    payload = _signed()
    payload["success_rate"] = 0.999

    with pytest.raises(EvalAttestationError, match="does not cover this payload"):
        verify_attestation(payload, _signer(), required=True)


def test_signature_from_another_key_is_rejected():
    payload = dict(EVAL)
    payload["attestation"] = build_attestation(payload, HmacSigner("baska-anahtar"))

    with pytest.raises(EvalAttestationError, match="signature rejected"):
        verify_attestation(payload, _signer(), required=True)


def test_unsigned_is_fine_when_not_required():
    verify_attestation(dict(EVAL), _signer(), required=False)


def test_unsigned_is_rejected_when_required():
    with pytest.raises(EvalAttestationError, match="not signed"):
        verify_attestation(dict(EVAL), _signer(), required=True)


def test_gate_fails_unsigned_when_attestation_is_required():
    checks = _checks(dict(EVAL), required=True)

    assert checks["eval_evidence"].passed is False
    assert "not signed" in checks["eval_evidence"].detail


def test_gate_passes_signed_when_attestation_is_required():
    checks = _checks(_signed(), required=True)

    assert checks["eval_evidence"].passed is True
    assert "signed" in checks["eval_evidence"].detail


@pytest.fixture
def require_attestation():
    os.environ["EVAL_REQUIRE_ATTESTATION"] = "true"
    get_settings.cache_clear()
    yield
    os.environ.pop("EVAL_REQUIRE_ATTESTATION", None)
    get_settings.cache_clear()


def test_api_rejects_unsigned_when_required(client, require_attestation):
    _register_and_version(client)

    res = client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL)

    assert res.status_code == 422
    assert res.json()["error"] == "eval_attestation"


def test_api_accepts_signed_and_promotes(client, require_attestation):
    _register_and_version(client)

    validated = client.post(f"{BASE}/versions/2.3.1:validate", json=_signed())
    assert validated.status_code == 200, validated.text

    promoted = client.post(f"{BASE}/versions/2.3.1:promote", json={"env": "prod"})

    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["env"] == "prod"


def test_promotion_rechecks_the_signature(client, require_attestation):
    """Terfi, saklanan eval'i yeniden doğrular: imza sonradan bozulursa kapı düşer."""
    from agent_registry import store
    from agent_registry.db import pool

    _register_and_version(client)
    assert client.post(f"{BASE}/versions/2.3.1:validate", json=_signed()).status_code == 200

    with pool().connection() as conn:
        conn.execute(
            "UPDATE agent_version "
            "SET eval_result = jsonb_set(eval_result, '{success_rate}', '0.999')"
        )
        conn.commit()

    res = client.post(f"{BASE}/versions/2.3.1:promote", json={"env": "prod"})

    assert res.status_code == 409
    assert res.json()["error"] == "gate_failed"
    assert "eval_evidence" in res.json()["message"]
    assert store.get_active_version(1) is None
