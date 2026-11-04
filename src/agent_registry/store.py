"""SQL erişim katmanı — iş kuralları burada YOK, yalnız veri."""

from typing import Any

from psycopg.types.json import Jsonb

from .db import connection


def _one(cur) -> dict[str, Any] | None:
    return cur.fetchone()


def insert_agent(
    *,
    name: str,
    tenant: str,
    owner_team: str,
    purpose: str,
    criticality: str,
    data_classification: str,
) -> dict:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO agent
                 (name, tenant, owner_team, purpose, criticality, data_classification)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, name, tenant, owner_team, purpose, created_at""",
            (name, tenant, owner_team, purpose, criticality, data_classification),
        )
        row = cur.fetchone()
        return row


def get_agent(tenant: str, name: str) -> dict | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM agent WHERE tenant = %s AND name = %s", (tenant, name))
        return _one(cur)


def retire_agent(tenant: str, name: str) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agent SET retired_at = now() WHERE tenant = %s AND name = %s",
            (tenant, name),
        )


def insert_version(
    *,
    agent_id: int,
    semver: str,
    manifest_json: dict,
    manifest_hash: str,
    git_commit: str | None,
    eval_suite: str | None,
    created_by: str,
) -> dict:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO agent_version
                 (agent_id, semver, manifest_json, manifest_hash, git_commit,
                  eval_suite, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id, agent_id, semver, state, manifest_hash, created_at""",
            (
                agent_id,
                semver,
                Jsonb(manifest_json),
                manifest_hash,
                git_commit,
                eval_suite,
                created_by,
            ),
        )
        row = cur.fetchone()
        return row


def get_version(agent_id: int, semver: str) -> dict | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM agent_version WHERE agent_id = %s AND semver = %s",
            (agent_id, semver),
        )
        return _one(cur)


def get_active_version(agent_id: int) -> dict | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM agent_version WHERE agent_id = %s AND state = 'active'",
            (agent_id,),
        )
        return _one(cur)


def set_state(version_id: int, state: str) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agent_version SET state = %s WHERE id = %s", (state, version_id))


def set_eval_result(version_id: int, eval_result: dict, suite: str) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agent_version SET eval_result = %s, eval_suite = %s WHERE id = %s",
            (Jsonb(eval_result), suite, version_id),
        )


def insert_promotion(
    *,
    from_version: int | None,
    to_version: int,
    gate_results: dict,
    approver: str,
    decision: str,
) -> dict:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO promotion
                 (from_version, to_version, gate_results_json, approver, decision)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING id, decision, decided_at""",
            (from_version, to_version, Jsonb(gate_results), approver, decision),
        )
        row = cur.fetchone()
        return row


def insert_deployment(
    *, version_id: int, env: str, deployed_by: str, config_hash: str | None
) -> dict:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO deployment (version_id, env, config_hash, deployed_by)
               VALUES (%s, %s, %s, %s) RETURNING id, env, deployed_at""",
            (version_id, env, config_hash, deployed_by),
        )
        row = cur.fetchone()
        return row


def insert_dependencies(version_id: int, deps: list[tuple[str, str]]) -> None:
    if not deps:
        return
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO dependency (version_id, kind, ref)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            [(version_id, kind, ref) for kind, ref in deps],
        )


def insert_audit(
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    before_hash: str | None = None,
    after_hash: str | None = None,
    detail: dict | None = None,
    source_ip: str | None = None,
) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO audit_event
                 (actor, action, object_type, object_id, before_hash, after_hash, detail, source_ip)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                actor,
                action,
                object_type,
                object_id,
                before_hash,
                after_hash,
                Jsonb(detail) if detail is not None else None,
                source_ip,
            ),
        )


def list_agents(tenant: str | None = None, owner: str | None = None) -> list[dict]:
    sql = """SELECT a.id, a.name, a.tenant, a.owner_team, a.purpose, a.retired_at,
                    v.semver AS active_version, v.created_at AS active_since
             FROM agent a
             LEFT JOIN agent_version v ON v.agent_id = a.id AND v.state = 'active'
             WHERE 1 = 1"""
    params: list[Any] = []
    if tenant:
        sql += " AND a.tenant = %s"
        params.append(tenant)
    if owner:
        sql += " AND a.owner_team = %s"
        params.append(owner)
    sql += " ORDER BY a.tenant, a.name"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def active_scopes_by_team(tenant: str) -> list[dict]:
    """Çakışma kontrolü: kiracıdaki aktif ajanların kapsamları."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT a.name, a.owner_team, v.semver, v.manifest_json->'data_scopes' AS scopes
               FROM agent a JOIN agent_version v ON v.agent_id = a.id
               WHERE a.tenant = %s AND v.state = 'active'""",
            (tenant,),
        )
        return cur.fetchall()


def audit_events(
    object_type: str | None = None, object_id: str | None = None, limit: int = 100
) -> list[dict]:
    sql = "SELECT * FROM audit_event WHERE 1 = 1"
    params: list[Any] = []
    if object_type:
        sql += " AND object_type = %s"
        params.append(object_type)
    if object_id:
        sql += " AND object_id = %s"
        params.append(object_id)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()
