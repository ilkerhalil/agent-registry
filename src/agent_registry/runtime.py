"""Runtime SDK — ajanı yalnız imzalı manifestle çalıştıran istemci.

Üç sözleşmenin ikincisi burada kapanır: runtime, registry'den gelen yanıtı
körü körüne kabul etmez. İmzayı, manifest hash'ini ve son kullanma tarihini
kendisi doğrular; doğrulayamazsa ajanı çalıştırmaz.

Registry erişilemezse son doğrulanmış kopya TTL dolana kadar kullanılır.
TTL dolmuşsa çalışma durur — imzasız veya süresi geçmiş manifest asla
çalıştırılmaz.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .manifest import AgentManifest
from .service import build_signed_payload
from .signers import HmacSigner, Signer, SignerError

DEFAULT_TIMEOUT = 5.0


class ResolutionError(Exception):
    """Manifest çözülemedi veya doğrulanamadı."""


@dataclass(frozen=True)
class ResolvedAgent:
    agent: str
    tenant: str
    version: str
    env: str
    manifest: dict
    manifest_hash: str
    signature: str
    expires_at: datetime
    from_cache: bool = False

    @property
    def manifest_obj(self) -> AgentManifest:
        return AgentManifest.model_validate(self.manifest)


def _default_fetch(url: str, api_key: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 404 / 401 / 403
        detail = exc.read().decode("utf-8", "ignore")[:200]
        raise ResolutionError(f"registry {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ResolutionError(f"registry unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResolutionError("registry response is not JSON") from exc


class AgentRuntime:
    """Registry'den imzalı manifest çözer; doğrulanmayan manifesti vermez.

    Örnek:
        runtime = AgentRuntime(
            base_url="http://127.0.0.1:8100",
            api_key="runtime-denizbank",
            signing_key=os.environ["REGISTRY_SIGNING_KEY"],
            tenant="denizbank",
        )
        agent = runtime.resolve("claim-triage", env="prod")
        agent.manifest_obj.purpose
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        signing_key: str,
        tenant: str,
        ttl_seconds: int = 300,
        cache_dir: str | Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        fetch: Callable[[str, str, float], dict] | None = None,
        verifier: Signer | None = None,
    ) -> None:
        if not signing_key and verifier is None:
            raise ResolutionError("verification key cannot be empty")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._signing_key = signing_key
        # HMAC varsayılan; Cosign gibi asimetrik backend'ler dışarıdan verilir.
        self._verifier: Signer = verifier or HmacSigner(signing_key)
        self.tenant = tenant
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "agent-registry"
        self._fetch = fetch or _default_fetch

    # -- genel ---------------------------------------------------------------

    def resolve(self, name: str, env: str = "prod") -> ResolvedAgent:
        """İmzalı manifesti çözer. Registry yoksa son geçerli kopyaya düşer."""
        try:
            payload = self._fetch(self._url(name, env), self._api_key, self.timeout)
        except ResolutionError as fetch_error:
            # Önbellek yoksa asıl sebep kaybolmasın: 401 ile "ağ yok" aynı mesaja
            # düşerse saha teşhisi imkânsız hâle gelir.
            try:
                return self._from_cache(name, env)
            except ResolutionError as cache_error:
                raise ResolutionError(f"{fetch_error} · {cache_error}") from fetch_error

        agent = self._verify(payload, expected_agent=name, expected_env=env)
        self._write_cache(name, env, payload)
        return agent

    def clear_cache(self, name: str, env: str = "prod") -> None:
        self._cache_path(name, env).unlink(missing_ok=True)

    # -- doğrulama -----------------------------------------------------------

    def _verify(self, payload: dict, *, expected_agent: str, expected_env: str) -> ResolvedAgent:
        required = (
            "agent",
            "tenant",
            "version",
            "env",
            "manifest",
            "manifest_hash",
            "signature",
            "expires_at",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise ResolutionError(f"resolve response is missing fields: {', '.join(missing)}")

        if payload["agent"] != expected_agent or payload["env"] != expected_env:
            raise ResolutionError("resolve response does not match the requested agent/env")
        if payload["tenant"] != self.tenant:
            raise ResolutionError("resolve response belongs to another tenant")

        try:
            manifest = AgentManifest.model_validate(payload["manifest"])
        except Exception as exc:  # pydantic doğrulama hatası
            raise ResolutionError(f"manifest schema is invalid: {str(exc)[:160]}") from exc

        # İmza yalnız manifest_hash'i kapsar; hash'i manifestten yeniden üretmezsek
        # imza, içeriği değiştirilmiş bir manifesti de doğrular.
        if manifest.sha256() != payload["manifest_hash"]:
            raise ResolutionError("manifest hash does not match the hash in the response")

        signed = build_signed_payload(
            agent=payload["agent"],
            tenant=payload["tenant"],
            version=payload["version"],
            env=payload["env"],
            manifest_hash=payload["manifest_hash"],
            expires_at=payload["expires_at"],
        )
        try:
            self._verifier.verify(signed, payload["signature"])
        except SignerError as exc:
            raise ResolutionError(f"signature verification failed: {exc}") from exc

        try:
            expires_at = datetime.fromisoformat(payload["expires_at"])
        except ValueError as exc:
            raise ResolutionError("expires_at is not a valid timestamp") from exc
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise ResolutionError("manifest has expired")

        return ResolvedAgent(
            agent=payload["agent"],
            tenant=payload["tenant"],
            version=payload["version"],
            env=payload["env"],
            manifest=payload["manifest"],
            manifest_hash=payload["manifest_hash"],
            signature=payload["signature"],
            expires_at=expires_at,
        )

    # -- önbellek ------------------------------------------------------------

    def _cache_path(self, name: str, env: str) -> Path:
        safe = urllib.parse.quote(f"{self.tenant}__{name}__{env}", safe="")
        return self.cache_dir / f"{safe}.json"

    def _write_cache(self, name: str, env: str, payload: dict) -> None:
        path = self._cache_path(name, env)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _from_cache(self, name: str, env: str) -> ResolvedAgent:
        path = self._cache_path(name, env)
        if not path.exists():
            raise ResolutionError("registry unreachable and no cached copy available")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ResolutionError("cached copy could not be read") from exc

        # Önbellekteki kopya da aynı doğrulamadan geçer: imza, hash, süre.
        try:
            agent = self._verify(payload, expected_agent=name, expected_env=env)
        except ResolutionError as exc:
            raise ResolutionError(f"cached copy is invalid: {exc}") from exc
        return ResolvedAgent(**{**agent.__dict__, "from_cache": True})

    def _url(self, name: str, env: str) -> str:
        return (
            f"{self.base_url}/v1/agents/{urllib.parse.quote(self.tenant, safe='')}"
            f"/{urllib.parse.quote(name, safe='')}/resolve?env={urllib.parse.quote(env, safe='')}"
        )
