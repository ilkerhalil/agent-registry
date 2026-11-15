"""CLI testleri — `agent-registry gate` dahil tüm komutlar."""

from __future__ import annotations

import json

import pytest

from agent_registry import cli
from agent_registry.config import get_settings

from .conftest import EVAL, MANIFEST


def _files(tmp_path, manifest=None, eval_result=None):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest or MANIFEST), encoding="utf-8")
    if eval_result is None:
        return manifest_path, None
    eval_path = tmp_path / "eval.json"
    eval_path.write_text(json.dumps(eval_result), encoding="utf-8")
    return manifest_path, eval_path


def test_gate_passes_for_valid_manifest(tmp_path, capsys):
    manifest, eval_result = _files(tmp_path, eval_result=EVAL)

    code = cli.main(
        [
            "gate",
            "--manifest",
            str(manifest),
            "--eval-result",
            str(eval_result),
            "--no-db",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "RESULT: PASS" in out
    assert "[PASS]" in out


def test_gate_fails_on_unknown_tool(tmp_path, capsys):
    manifest, eval_result = _files(
        tmp_path, dict(MANIFEST, tools=["policy_lookup", "yok_arac"]), eval_result=EVAL
    )

    code = cli.main(
        ["gate", "--manifest", str(manifest), "--eval-result", str(eval_result), "--no-db"]
    )

    assert code == 1
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_gate_json_output_is_machine_readable(tmp_path, capsys):
    manifest, eval_result = _files(tmp_path, eval_result=EVAL)

    cli.main(
        [
            "gate",
            "--manifest",
            str(manifest),
            "--eval-result",
            str(eval_result),
            "--no-db",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert len(payload["checks"]) == 8


def test_gate_without_eval_result_fails(tmp_path, capsys):
    manifest, _ = _files(tmp_path)

    code = cli.main(["gate", "--manifest", str(manifest), "--no-db"])

    assert code == 1
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_gate_uses_registry_for_conflicts(tmp_path, client):
    # Ajan kayıtlıyken kapı DB yolunu da çalıştırır (çakışma yoksa geçer).
    client.post(
        "/v1/agents",
        json={
            "name": "claim-triage",
            "tenant": "denizbank",
            "owner_team": "hasar-operasyon",
            "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
        },
    )
    manifest, eval_result = _files(tmp_path, eval_result=EVAL)

    code = cli.main(["gate", "--manifest", str(manifest), "--eval-result", str(eval_result)])

    assert code == 0


def test_agents_lists_inventory(tmp_path, client, capsys):
    client.post(
        "/v1/agents",
        json={
            "name": "claim-triage",
            "tenant": "denizbank",
            "owner_team": "hasar-operasyon",
            "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
        },
    )

    assert cli.main(["agents", "--tenant", "denizbank"]) == 0
    assert "claim-triage" in capsys.readouterr().out


def test_init_db_applies_schema(capsys):
    assert cli.main(["init-db"]) == 0
    assert "schema applied" in capsys.readouterr().out


def test_serve_refuses_without_api_keys():
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("REGISTRY_API_KEYS", raising=False)
        get_settings.cache_clear()
        assert cli.main(["serve"]) == 2
    # Ortam değişkeni geri geldikten SONRA önbelleği temizle.
    get_settings.cache_clear()


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        cli.main([])
