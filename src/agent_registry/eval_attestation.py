"""Eval sonucunun imzalı kanıtı.

Sayıyı kim ürettiyse imzayı o atsın: registry imzayı doğrular, böylece elle
yazılmış bir `success_rate` kapıdan geçemez. İmza, gövdenin `attestation`
alanı hariç kanonik hâlini kapsar — imza eklendikten sonra sayı değişirse
doğrulama düşer.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .artifacts import canonical_bytes
from .signers import Signer, SignerError


class EvalAttestationError(Exception):
    """Eval imzası yok, kapsamıyor ya da geçersiz."""


def attestation_body(payload: dict[str, Any]) -> bytes:
    return canonical_bytes({key: value for key, value in payload.items() if key != "attestation"})


def attestation_hash(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(attestation_body(payload)).hexdigest()


def build_attestation(payload: dict[str, Any], signer: Signer) -> dict[str, str]:
    body = attestation_body(payload)
    return {
        "signed_hash": attestation_hash(payload),
        "signature": signer.sign(body.decode("utf-8")),
    }


def verify_attestation(
    payload: dict[str, Any], signer: Signer | None, *, required: bool = False
) -> None:
    attestation = payload.get("attestation")
    if not attestation:
        if required:
            raise EvalAttestationError(
                "eval result is not signed; the registry requires an attestation"
            )
        return

    if signer is None:
        raise EvalAttestationError("eval result is signed but no verifier is configured")

    signed_hash = attestation.get("signed_hash")
    if signed_hash != attestation_hash(payload):
        raise EvalAttestationError("attestation does not cover this payload")

    try:
        signer.verify(attestation_body(payload).decode("utf-8"), attestation.get("signature") or "")
    except SignerError as exc:
        raise EvalAttestationError(f"eval signature rejected: {exc}") from exc
