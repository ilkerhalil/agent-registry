"""Veritabanı bağlantısı ve şema kurulumu."""

from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_settings().database_url,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_schema() -> None:
    """sql/001_schema.sql dosyasını uygular (idempotent)."""
    schema = Path(__file__).resolve().parents[2] / "sql" / "001_schema.sql"
    sql = schema.read_text(encoding="utf-8")
    with pool().connection() as conn:
        conn.execute(sql)
        conn.commit()
