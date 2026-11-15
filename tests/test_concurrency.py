"""Eşzamanlı terfi yarışı — "tek aktif sürüm" garantisi gerçekten tutuyor mu?"""

from __future__ import annotations

import threading

import pytest
from psycopg.errors import IntegrityError

from agent_registry import service
from agent_registry.db import connection
from agent_registry.service import RegistryError

from .conftest import EVAL, MANIFEST

AGENT = {
    "name": "claim-triage",
    "tenant": "denizbank",
    "owner_team": "hasar-operasyon",
    "purpose": "Hasar ihbarını branşa ve uzmanlığa yönlendirir",
}


def _prepare_two_versions(client):
    client.post("/v1/agents", json=AGENT)
    for semver in ("2.3.1", "2.3.2"):
        client.post(
            "/v1/agents/denizbank/claim-triage/versions",
            json={"manifest": dict(MANIFEST, version=semver)},
        )
        client.post(f"/v1/agents/denizbank/claim-triage/versions/{semver}:validate", json=EVAL)


def _active_count(agent: str = "claim-triage") -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM agent_version v JOIN agent a ON a.id = v.agent_id
               WHERE a.name = %s AND v.state = 'active'""",
            (agent,),
        )
        return cur.fetchone()["n"]


def test_schema_enforces_single_active_index():
    """Yarışın son savunma hattı DB'de: kısmi benzersiz indeks var mı?"""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT indexname FROM pg_indexes
               WHERE tablename IN ('agent_version', 'deployment')
                 AND indexdef LIKE '%UNIQUE%'"""
        )
        indexes = {row["indexname"] for row in cur.fetchall()}

    assert "agent_version_one_active" in indexes
    assert "deployment_one_active" in indexes


def test_db_rejects_two_active_versions_for_the_same_agent(client):
    _prepare_two_versions(client)
    service.promote_version(
        tenant="denizbank", name="claim-triage", semver="2.3.1", actor="ci-bot", env="prod"
    )

    # Kısıtı doğrudan zorla: aynı ajan için ikinci 'active' sürüm yazılamamalı.
    with pytest.raises(IntegrityError), connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE agent_version SET state = 'active'
               WHERE agent_id = (SELECT id FROM agent WHERE name = 'claim-triage')
                 AND semver = '2.3.2'"""
        )


def test_concurrent_promotion_keeps_exactly_one_active(client):
    _prepare_two_versions(client)

    results: list[str] = []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def promote(semver: str) -> None:
        barrier.wait(timeout=5)
        try:
            service.promote_version(
                tenant="denizbank",
                name="claim-triage",
                semver=semver,
                actor="ci-bot",
                env="prod",
            )
            outcome = f"ok:{semver}"
        except Exception as exc:  # noqa: BLE001 - yarışan taraf DB hatası verebilir
            outcome = f"{type(exc).__name__}:{semver}"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=promote, args=(v,)) for v in ("2.3.1", "2.3.2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert len(results) == 2, f"bir iş parçacığı sonuç üretmedi: {results}"
    # Zamanlamaya göre biri ya da ikisi de geçebilir; değişmeyen tek şey son durum.
    assert _active_count() == 1, f"birden fazla aktif sürüm: {results}"

    active = [v for v in service.inventory("denizbank") if v["name"] == "claim-triage"]
    assert active[0]["active_version"] in {"2.3.1", "2.3.2"}


def test_second_promotion_moves_the_active_pointer(client):
    _prepare_two_versions(client)

    service.promote_version(
        tenant="denizbank", name="claim-triage", semver="2.3.1", actor="ci-bot", env="prod"
    )
    service.promote_version(
        tenant="denizbank", name="claim-triage", semver="2.3.2", actor="ci-bot", env="prod"
    )

    active = [v for v in service.inventory("denizbank") if v["name"] == "claim-triage"]
    assert active[0]["active_version"] == "2.3.2"
    assert _active_count() == 1


def test_promoting_unvalidated_version_is_rejected(client):
    client.post("/v1/agents", json=AGENT)
    client.post("/v1/agents/denizbank/claim-triage/versions", json={"manifest": MANIFEST})

    with pytest.raises(RegistryError) as exc:
        service.promote_version(
            tenant="denizbank", name="claim-triage", semver="2.3.1", actor="ci-bot", env="prod"
        )

    assert exc.value.code == "not_validated"
