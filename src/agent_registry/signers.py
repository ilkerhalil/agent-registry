"""İmza backend'leri: HMAC (varsayılan) ve Cosign.

Arayüz dar tutuldu — `sign(payload) -> str` ve `verify(payload, signature)`.
HMAC simetrik: aynı anahtar imzalar ve doğrular. Cosign asimetrik: özel anahtar
imzalar, açık anahtar doğrular; bu yüzden resolve eden taraf yalnız .pub dosyasına
ihtiyaç duyar ve sızdırılabilecek bir sır taşımaz.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from . import signing
from .signing import SignerError

COSIGN_PREFIX = "cosign:"


class Signer(Protocol):
    def sign(self, payload: str) -> str: ...

    def verify(self, payload: str, signature: str) -> None: ...


class HmacSigner:
    def __init__(self, key: str) -> None:
        if not key:
            raise SignerError("signing key cannot be empty")
        self.key = key

    def sign(self, payload: str) -> str:
        return signing.sign(payload, self.key)

    def verify(self, payload: str, signature: str) -> None:
        signing.verify(payload, signature, self.key)


class CosignSigner:
    """`cosign sign-blob` / `verify-blob` üzerinden imzalama.

    Özel anahtar yalnız imzalayan tarafta; doğrulayan taraf `pub_key_path` ile
    çalışır ve `key_path` vermek zorunda değildir.
    """

    def __init__(
        self,
        *,
        key_path: str | Path | None = None,
        pub_key_path: str | Path | None = None,
        password: str | None = None,
        binary: str | None = None,
        timeout: int = 60,
    ) -> None:
        self.binary = binary or os.environ.get("COSIGN_BIN") or "cosign"
        if not shutil.which(self.binary) and not Path(self.binary).exists():
            raise SignerError(f"cosign not found: {self.binary}")
        self.key_path = Path(key_path) if key_path else None
        self.pub_key_path = Path(pub_key_path) if pub_key_path else None
        self.password = password or os.environ.get("COSIGN_PASSWORD", "")
        self.timeout = timeout

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        env = {**os.environ, "COSIGN_PASSWORD": self.password}
        return subprocess.run(
            [self.binary, *args],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=env,
        )

    def sign(self, payload: str) -> str:
        if not self.key_path:
            raise SignerError("a private key is required to sign")
        with tempfile.TemporaryDirectory() as td:
            blob = Path(td) / "payload.json"
            bundle = Path(td) / "bundle.json"
            blob.write_text(payload, encoding="utf-8")
            # cosign >=2.4 imzayı "bundle" içinde üretir; bundle imza + doğrulama
            # malzemesini (anahtar ipucu, şeffaflık kaydı) birlikte taşır.
            result = self._run(
                [
                    "sign-blob",
                    "--key",
                    str(self.key_path),
                    "--yes",
                    "--bundle",
                    str(bundle),
                    str(blob),
                ]
            )
            if result.returncode != 0:
                raise SignerError(f"cosign sign-blob failed: {result.stderr[:200]}")
            raw = bundle.read_bytes()
        return COSIGN_PREFIX + base64.b64encode(raw).decode("ascii")

    def verify(self, payload: str, signature: str) -> None:
        if not signature.startswith(COSIGN_PREFIX):
            raise SignerError("signature is not in cosign format")
        if not self.pub_key_path:
            raise SignerError("a public key is required to verify")
        try:
            raw = base64.b64decode(signature.removeprefix(COSIGN_PREFIX))
        except Exception as exc:  # noqa: BLE001
            raise SignerError("signature could not be decoded") from exc
        with tempfile.TemporaryDirectory() as td:
            blob = Path(td) / "payload.json"
            bundle = Path(td) / "bundle.json"
            blob.write_text(payload, encoding="utf-8")
            bundle.write_bytes(raw)
            result = self._run(
                ["verify-blob", "--key", str(self.pub_key_path), "--bundle", str(bundle), str(blob)]
            )
        if result.returncode != 0:
            raise SignerError(f"cosign verify-blob failed: {result.stderr[:200]}")


def get_signer(settings) -> Signer:
    """Ayarlara göre imza backend'i."""
    if settings.signing_backend == "cosign":
        return CosignSigner(
            key_path=settings.cosign_key_path,
            pub_key_path=settings.cosign_pub_key_path,
            password=settings.cosign_password,
        )
    return HmacSigner(settings.registry_signing_key)
