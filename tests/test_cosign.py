"""M3: Cosign imza backend'i — gerçek anahtarla imzala/doğrula.

cosign ikilisi yoksa testler atlanır (CI'da kurulu değil).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_registry.signers import CosignSigner, HmacSigner, SignerError

COSIGN = os.environ.get("COSIGN_BIN") or shutil.which("cosign")
pytestmark = pytest.mark.skipif(not COSIGN, reason="cosign ikilisi yok")
BIN = COSIGN or "cosign"


@pytest.fixture(scope="module")
def keypair(tmp_path_factory):
    directory = tmp_path_factory.mktemp("cosign-keys")
    env = {**os.environ, "COSIGN_PASSWORD": "test-parola"}
    result = subprocess.run(
        [BIN, "generate-key-pair", "--output-key-prefix", str(directory / "cosign")],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(f"anahtar üretilemedi: {result.stderr[:200]}")
    return directory / "cosign.key", directory / "cosign.pub"


def test_cosign_sign_and_verify(keypair):
    key, pub = keypair
    signer = CosignSigner(key_path=key, pub_key_path=pub, password="test-parola", binary=COSIGN)
    payload = '{"agent":"claim-triage","version":"2.3.1"}'

    signature = signer.sign(payload)

    assert signature.startswith("cosign:")
    signer.verify(payload, signature)


def test_cosign_rejects_tampered_payload(keypair):
    key, pub = keypair
    signer = CosignSigner(key_path=key, pub_key_path=pub, password="test-parola", binary=COSIGN)
    signature = signer.sign('{"version":"2.3.1"}')

    with pytest.raises(SignerError, match="verify-blob"):
        signer.verify('{"version":"9.9.9"}', signature)


def test_cosign_rejects_wrong_prefix(keypair):
    _, pub = keypair
    signer = CosignSigner(pub_key_path=pub, binary=COSIGN)

    with pytest.raises(SignerError, match="cosign format"):
        signer.verify("payload", "hmac-sha256:abc")


def test_hmac_signer_roundtrip():
    signer = HmacSigner("gizli-anahtar")
    signature = signer.sign("govde")

    assert signature.startswith("hmac-sha256:")
    signer.verify("govde", signature)

    with pytest.raises(SignerError):
        signer.verify("baska-govde", signature)


def test_missing_cosign_binary_raises():
    with pytest.raises(SignerError, match="not found"):
        CosignSigner(binary="/nonexistent/cosign", pub_key_path=Path("/tmp/x.pub"))
