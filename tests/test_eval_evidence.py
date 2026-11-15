"""A: eval sonucu kanıt ister — çıplak `success_rate` kapıdan geçmez."""

from __future__ import annotations

import pytest

from agent_registry.eval_result import EvalEvidenceError, parse_eval_result
from agent_registry.gate import load_catalogs, run_gate

from .conftest import EVAL, MANIFEST

CATALOGS = load_catalogs("catalogs")
BASE = "/v1/agents/denizbank/claim-triage"


def _checks(evals: dict):
    return {c.name: c for c in run_gate(MANIFEST, evals, CATALOGS, conflicts=[])}


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


def test_complete_evidence_is_accepted():
    parsed = parse_eval_result(EVAL)

    assert parsed.total_cases == 300
    assert parsed.dataset_hash.startswith("sha256:")
    assert parsed.harness_version == "evalkit-1.3.0"


@pytest.mark.parametrize(
    "missing", ["dataset_hash", "harness_version", "started_at", "finished_at"]
)
def test_missing_evidence_field_is_rejected(missing):
    payload = {k: v for k, v in EVAL.items() if k != missing}

    with pytest.raises(EvalEvidenceError, match=missing):
        parse_eval_result(payload)


def test_case_counts_must_add_up():
    payload = {**EVAL, "total_cases": 999}

    with pytest.raises(EvalEvidenceError, match="add up to 300"):
        parse_eval_result(payload)


def test_finished_before_started_is_rejected():
    payload = {**EVAL, "finished_at": EVAL["started_at"]}

    with pytest.raises(EvalEvidenceError, match="finished_at must be after started_at"):
        parse_eval_result(payload)


def test_naive_timestamps_are_rejected():
    payload = {**EVAL, "started_at": "2026-11-14T09:12:03", "finished_at": "2026-11-14T09:41:27"}

    with pytest.raises(EvalEvidenceError, match="timezone"):
        parse_eval_result(payload)


def test_dataset_hash_must_look_like_a_digest():
    payload = {**EVAL, "dataset_hash": "not-a-hash"}

    with pytest.raises(EvalEvidenceError, match="dataset_hash"):
        parse_eval_result(payload)


def test_gate_marks_eval_evidence_separately_from_thresholds():
    checks = _checks({k: v for k, v in EVAL.items() if k != "dataset_hash"})

    assert checks["eval_evidence"].passed is False
    assert "dataset_hash" in checks["eval_evidence"].detail
    # eşik kontrolü ayrı satır: kanıt eksik olsa da sayı kendi başına değerlendirilir
    assert checks["eval"].passed is True


def test_zero_cases_is_not_evidence():
    payload = {**EVAL, "total_cases": 0, "suites": {}}

    with pytest.raises(EvalEvidenceError, match="greater than 0"):
        parse_eval_result(payload)


def test_api_rejects_bare_success_rate(client):
    _register_and_version(client)
    bare = {"suite": EVAL["suite"], "success_rate": 0.99, "suites": EVAL["suites"]}

    res = client.post(f"{BASE}/versions/2.3.1:validate", json=bare)

    assert res.status_code == 422
    assert res.json()["error"] == "eval_evidence"
    # eksik kanıt alanlarının hepsi tek mesajda: CI çıktısı tek turda düzeltilebilsin
    for field in ("dataset_hash", "harness_version", "started_at", "finished_at"):
        assert field in res.json()["message"]


def test_api_accepts_complete_evidence(client):
    _register_and_version(client)

    res = client.post(f"{BASE}/versions/2.3.1:validate", json=EVAL)

    assert res.status_code == 200, res.text
    assert res.json()["state"] == "validated"
