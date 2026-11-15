"""Uçtan uca demo: kayıt → sürüm → eval → terfi → imzalı resolve → denetim.

Çalıştırma:
    docker compose up -d db
    uv run python examples/demo.py

Demo gerçek Postgres'e yazar; tekrar çalıştırıldığında aynı ajan adını
kullanmaz (her çalıştırmada yeni bir sürüm numarası üretir).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault(
    "REGISTRY_API_KEYS",
    json.dumps(
        {
            "demo-publisher": {"actor": "ci-bot", "roles": ["publisher"], "tenants": ["denizbank"]},
            "demo-runtime": {"actor": "runtime", "roles": ["runtime"], "tenants": ["denizbank"]},
            "demo-security": {"actor": "sec-bot", "roles": ["security"], "tenants": ["*"]},
        }
    ),
)
os.environ.setdefault("REGISTRY_SIGNING_KEY", "demo-signing-key")

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent_registry.api import app  # noqa: E402
from agent_registry.db import init_schema  # noqa: E402
from agent_registry.runtime import AgentRuntime, ResolutionError  # noqa: E402

PUB = {"Authorization": "Bearer demo-publisher"}
RUN = {"Authorization": "Bearer demo-runtime"}
SEC = {"Authorization": "Bearer demo-security"}
ROOT = Path(__file__).resolve().parent.parent
AGENT = "claim-triage"
ENV = "prod"


def step(msg: str) -> None:
    print(f"\n▸ {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def main() -> int:
    init_schema()
    client = TestClient(app)

    manifest = yaml.safe_load((ROOT / "examples" / "claim-triage.manifest.yaml").read_text(encoding="utf-8"))
    evals = json.loads((ROOT / "examples" / "claim-triage.eval.json").read_text(encoding="utf-8"))

    # Her çalıştırmada yeni sürüm: demo tekrar edilebilir olsun, kayıt çakışmasın.
    version = f"2.3.{int(datetime.now(UTC).timestamp()) % 1000}"
    manifest["version"] = version

    step("1. Ajan kaydı")
    res = client.post(
        "/v1/agents",
        headers=PUB,
        json={
            "name": AGENT,
            "tenant": "denizbank",
            "owner_team": "hasar-operasyon",
            "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
            "criticality": "high",
            "data_classification": "confidential",
        },
    )
    if res.status_code == 409:
        ok("ajan zaten kayıtlı — devam")
    else:
        assert res.status_code == 201, res.text
        ok("ajan kaydedildi")

    step(f"2. Sürüm {version} (değişmez manifest)")
    res = client.post(
        f"/v1/agents/denizbank/{AGENT}/versions",
        headers=PUB,
        json={"manifest": manifest, "git_commit": "demo"},
    )
    assert res.status_code in (200, 201), res.text
    ok(f"manifest_hash {res.json()['manifest_hash'][:24]}…")

    step("3. Eval sonucu + doğrulama")
    res = client.post(
        f"/v1/agents/denizbank/{AGENT}/versions/{version}:validate",
        headers=PUB,
        json=evals,
    )
    assert res.status_code == 200, res.text
    ok(f"eval geçti · success_rate {evals['success_rate']}")

    step("4. Terfi (kapı sunucuda yeniden koşar)")
    res = client.post(
        f"/v1/agents/denizbank/{AGENT}/versions/{version}:promote",
        headers=PUB,
        json={"env": ENV},
    )
    assert res.status_code == 200, res.text
    ok(f"{ENV} ortamında aktif")

    step("5. Runtime resolve — imzalı manifest")
    res = client.get(f"/v1/agents/denizbank/{AGENT}/resolve?env={ENV}", headers=RUN)
    assert res.status_code == 200, res.text
    payload = res.json()
    ok(f"version {payload['version']} · imza {payload['signature'][:22]}…")

    step("6. Runtime SDK doğrulaması")

    def fetch(url: str, api_key: str, timeout: float) -> dict:
        path = url.split("registry.demo", 1)[1]
        r = client.get(path, headers={"Authorization": f"Bearer {api_key}"})
        r.raise_for_status()
        return r.json()

    runtime = AgentRuntime(
        base_url="http://registry.demo",
        api_key="demo-runtime",
        signing_key=os.environ["REGISTRY_SIGNING_KEY"],
        tenant="denizbank",
        cache_dir=ROOT / ".demo-cache",
        fetch=fetch,
    )
    agent = runtime.resolve(AGENT, env=ENV)
    ok(f"SDK imzayı doğruladı · {agent.agent}@{agent.version}")

    tampered = json.loads(json.dumps(payload))
    tampered["manifest"]["data_scopes"] = ["claims:read", "*:*"]
    try:
        runtime._verify(tampered, expected_agent=AGENT, expected_env=ENV)
    except ResolutionError as exc:
        ok(f"kurcalanmış manifest reddedildi → {exc}")
    else:
        print("  ✗ kurcalanmış manifest kabul edildi!")
        return 1

    step("7. Denetim izi (append-only)")
    res = client.get("/v1/audit?limit=5", headers=SEC)
    assert res.status_code == 200, res.text
    for event in res.json()["events"][:5]:
        print(f"  · {event.get('action', '?'):<28} {event.get('object_type', '')}")

    print("\nDemo tamam: kayıt → sürüm → eval → terfi → imzalı resolve → denetim.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
