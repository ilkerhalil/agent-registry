"""Manifest imzalama.

v0.1 varsayılanı HMAC-SHA256 (tek anahtar, simetrik).
Arayüz bilinçli olarak dar tutuldu: ileride Cosign/Sigstore backend'i
aynı imzayı üretmek için `sign`/`verify` çiftini uygular.
"""

import hashlib
import hmac

PREFIX = "hmac-sha256:"


class SignerError(Exception):
    """Tüm imza backend'lerinin ortak hata tipi."""


class SignatureError(SignerError):
    pass


def sign(payload: str, key: str) -> str:
    if not key:
        raise SignatureError("signing key cannot be empty")
    digest = hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return PREFIX + digest


def verify(payload: str, signature: str, key: str) -> None:
    if not signature or not signature.startswith(PREFIX):
        raise SignatureError("signature format is invalid")
    expected = sign(payload, key)
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("signature does not match the payload")
