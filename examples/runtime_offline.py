"""Runtime SDK without a network: cache fallback, TTL expiry, tamper rejection.

Run:  uv run python examples/runtime_offline.py

Nothing here needs Postgres or a server. The point is what the SDK refuses to do
when the registry is gone.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_registry.manifest import AgentManifest
from agent_registry.runtime import AgentRuntime, ResolutionError
from agent_registry.service import build_signed_payload
from agent_registry.signing import sign

KEY = "example-signing-key"
TENANT = "acme"


def payload(manifest: dict, version: str, expires_in: int = 300) -> dict:
    """A response shaped exactly like GET /v1/agents/{tenant}/{name}/resolve."""
    obj = AgentManifest.model_validate(manifest)
    expires = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
    signed = build_signed_payload(
        agent=obj.agent,
        tenant=obj.tenant,
        version=version,
        env="prod",
        manifest_hash=obj.sha256(),
        expires_at=expires,
    )
    return {
        "agent": obj.agent,
        "tenant": obj.tenant,
        "version": version,
        "env": "prod",
        "manifest": manifest,
        "manifest_hash": obj.sha256(),
        "signature": sign(signed, KEY),
        "expires_at": expires,
    }


MANIFEST = {
    "agent": "kb-rag",
    "version": "0.9.2",
    "owner": "support-ops",
    "tenant": TENANT,
    "purpose": "Answers from support articles with citations",
    "model_policy": {
        "primary": "embedding-small",
        "budget_usd_per_task": 0.006,
        "max_latency_p95_ms": 4000,
    },
    "tools": ["kb_search"],
    "data_scopes": ["kb:read"],
    "evals": {"suite": "kb-rag-v1", "min_success_rate": 0.88},
    "sla": {"availability": 99.0, "fallback": "show article links"},
}


def main() -> int:
    cache = Path(tempfile.mkdtemp(prefix="agent-registry-example-"))
    live = payload(MANIFEST, "0.9.2")

    up = True

    def fetch(url: str, api_key: str, timeout: float) -> dict:
        if not up:
            raise ResolutionError("registry unreachable: connection refused")
        return live

    runtime = AgentRuntime(
        base_url="http://registry.local",
        api_key="rt-1",
        signing_key=KEY,
        tenant=TENANT,
        cache_dir=cache,
        fetch=fetch,
    )

    print("1. registry is up")
    agent = runtime.resolve("kb-rag")
    print(f"   ok  {agent.agent}@{agent.version} from_cache={agent.from_cache}")

    print("2. registry goes down — the verified copy is reused")
    up = False
    agent = runtime.resolve("kb-rag")
    print(f"   ok  {agent.agent}@{agent.version} from_cache={agent.from_cache}")

    print("3. the cached copy expires — refuse to start")
    # A copy that was validly signed but has run out: writing it by hand is the
    # only way to simulate five minutes of clock time in a short example.
    expired = payload(MANIFEST, "0.9.2", expires_in=-1)
    runtime._cache_path("kb-rag", "prod").write_text(json.dumps(expired))
    try:
        runtime.resolve("kb-rag")
    except ResolutionError as exc:
        print(f"   ok  refused: {exc}")
    else:
        print("   FAIL expired cache was served")
        return 1

    print("4. tampered manifest — signature verifies, hash does not")
    tampered = json.loads(json.dumps(payload(MANIFEST, "0.9.2")))
    tampered["manifest"]["data_scopes"] = ["kb:read", "*:*"]
    try:
        runtime._verify(tampered, expected_agent="kb-rag", expected_env="prod")
    except ResolutionError as exc:
        print(f"   ok  refused: {exc}")
    else:
        print("   FAIL tampered manifest accepted")
        return 1

    print("\nDone. No server, no database — only what the SDK is willing to trust.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
