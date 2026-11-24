import os
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

_pool: "psycopg2.pool.ThreadedConnectionPool | None" = None
_pool_lock = threading.Lock()


def _get_pool() -> "psycopg2.pool.ThreadedConnectionPool":
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=20,
                    dsn=DATABASE_URL,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
    return _pool


@contextmanager
def get_db():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
