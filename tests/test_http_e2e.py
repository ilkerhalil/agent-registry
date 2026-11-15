"""Gerçek HTTP testi — uvicorn açık sokette, runtime SDK urllib ile bağlanıyor.

Diğer testler in-process TestClient kullanıyor; bu dosya ağ üzerinden giden yolu
(runtime._default_fetch, kimlik doğrulama başlıkları, gerçek JSON) kapsar.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn

from agent_registry.api import app
from agent_registry.config import get_settings
from agent_registry.runtime import AgentRuntime, ResolutionError

from .conftest import EVAL, MANIFEST, PUBLISHER


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_server():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=0.5) as resp:
                if resp.status == 200:
                    break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("uvicorn başlamadı")

    yield base
    server.should_exit = True
    thread.join(timeout=5)


def _seed(base: str) -> None:
    import json

    def post(path: str, body: dict) -> None:
        req = urllib.request.Request(
            f"{base}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={**PUBLISHER, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status in (200, 201), resp.read()

    post(
        "/v1/agents",
        {
            "name": "claim-triage",
            "tenant": "denizbank",
            "owner_team": "hasar-operasyon",
            "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
        },
    )
    post("/v1/agents/denizbank/claim-triage/versions", {"manifest": MANIFEST})
    post("/v1/agents/denizbank/claim-triage/versions/2.3.1:validate", EVAL)
    post("/v1/agents/denizbank/claim-triage/versions/2.3.1:promote", {"env": "prod"})


def test_runtime_resolves_over_real_http(live_server, tmp_path):
    _seed(live_server)
    runtime = AgentRuntime(
        base_url=live_server,
        api_key="runtime-denizbank",
        signing_key=get_settings().registry_signing_key,
        tenant="denizbank",
        cache_dir=tmp_path,
    )

    agent = runtime.resolve("claim-triage", env="prod")

    assert agent.version == "2.3.1"
    assert agent.manifest_hash.startswith("sha256:")
    assert agent.manifest_obj.purpose.startswith("Hasar ihbarını")
    assert agent.from_cache is False


def test_bad_api_key_surfaces_401(live_server, tmp_path):
    _seed(live_server)
    runtime = AgentRuntime(
        base_url=live_server,
        api_key="yanlis-anahtar",
        signing_key=get_settings().registry_signing_key,
        tenant="denizbank",
        cache_dir=tmp_path,
    )

    with pytest.raises(ResolutionError, match="registry 401"):
        runtime.resolve("claim-triage", env="prod")


def test_unknown_agent_surfaces_404(live_server, tmp_path):
    runtime = AgentRuntime(
        base_url=live_server,
        api_key="runtime-denizbank",
        signing_key=get_settings().registry_signing_key,
        tenant="denizbank",
        cache_dir=tmp_path,
    )

    with pytest.raises(ResolutionError, match="registry 404"):
        runtime.resolve("olmayan-ajan", env="prod")


def test_unreachable_registry_is_reported(live_server, tmp_path):
    runtime = AgentRuntime(
        base_url=f"http://127.0.0.1:{_free_port()}",
        api_key="runtime-denizbank",
        signing_key=get_settings().registry_signing_key,
        tenant="denizbank",
        timeout=0.5,
        cache_dir=tmp_path,
    )

    with pytest.raises(ResolutionError, match="unreachable"):
        runtime.resolve("claim-triage", env="prod")
