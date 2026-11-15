"""Veritabanı bağlantısı, transaction ve şema kurulumu."""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None
_active_conn: ContextVar[Connection | None] = ContextVar("active_conn", default=None)


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


@contextmanager
def transaction() -> Iterator[Connection]:
    """Birden çok store çağrısını tek transaction'da birleştirir.

    Blok normal biterse commit, istisnada rollback (psycopg_pool davranışı).
    İç içe çağrılar dıştaki transaction'a katılır.
    """
    active = _active_conn.get()
    if active is not None:
        yield active
        return
    with pool().connection() as conn:
        token = _active_conn.set(conn)
        try:
            yield conn
        finally:
            _active_conn.reset(token)


def connection():
    """Aktif transaction varsa onu, yoksa havuzdan yeni bağlantı verir."""
    active = _active_conn.get()
    if active is not None:
        return nullcontext(active)
    return pool().connection()


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_schema() -> None:
    """sql/*.sql dosyalarını sırayla uygular (her dosya idempotent olmalı)."""
    sql_dir = Path(__file__).resolve().parents[2] / "sql"
    statements = [p.read_text(encoding="utf-8") for p in sorted(sql_dir.glob("*.sql"))]
    if not statements:
        raise RuntimeError(f"şema dosyası bulunamadı: {sql_dir}")
    with pool().connection() as conn:
        for sql in statements:
            conn.execute(sql)
        conn.commit()
