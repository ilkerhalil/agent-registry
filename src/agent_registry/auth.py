"""Kimlik doğrulama ve yetkilendirme: Bearer API anahtarları.

v0.1'de anahtarlar `REGISTRY_API_KEYS` ortam değişkeninden JSON olarak okunur:

    {"<anahtar>": {"actor": "...", "roles": ["publisher"], "tenants": ["denizbank"]}}

Roller: `runtime` (yalnız resolve), `publisher` (yazma uçları), `security` (block, audit).
Anahtar yoksa istek reddedilir (fail-closed). v0.2'de OIDC/mTLS backend'i aynı
`Principal` arayüzünü üretmek için takılabilir.
"""

import hashlib
import hmac
from dataclasses import dataclass

from .config import get_settings


@dataclass(frozen=True)
class Principal:
    actor: str
    roles: frozenset[str]
    tenants: frozenset[str]

    def has_role(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))

    def allows_tenant(self, tenant: str) -> bool:
        return "*" in self.tenants or tenant in self.tenants


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401, code: str = "unauthorized"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def authenticate(token: str) -> Principal:
    """Sunulan anahtarı yapılandırılmış anahtarlarla sabit zamanlı karşılaştırır."""
    keys = get_settings().api_keys
    if not keys:
        raise AuthError(
            "kimlik doğrulama yapılandırılmamış (REGISTRY_API_KEYS)",
            status=503,
            code="auth_unconfigured",
        )
    presented = _digest(token)
    for raw_key, spec in keys.items():
        if hmac.compare_digest(presented, _digest(raw_key)):
            return Principal(
                actor=spec.actor,
                roles=frozenset(spec.roles),
                tenants=frozenset(spec.tenants),
            )
    raise AuthError("geçersiz veya eksik API anahtarı")


def require_role(principal: Principal, *roles: str) -> None:
    if not principal.has_role(*roles):
        raise AuthError(
            f"bu işlem için yetki yok (gerekli rol: {', '.join(roles)})",
            status=403,
            code="forbidden",
        )


def require_tenant(principal: Principal, tenant: str) -> None:
    if not principal.allows_tenant(tenant):
        raise AuthError(f"{tenant} kiracısına erişim yok", status=403, code="forbidden_tenant")
