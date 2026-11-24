from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import get_settings


_pool: AsyncConnectionPool | None = None


async def open_pool() -> None:
    global _pool
    if _pool is not None:
        return
    settings = get_settings()
    _pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": False, "row_factory": dict_row},
    )
    await _pool.open(wait=True, timeout=10)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized")
    return _pool


@asynccontextmanager
async def transaction():
    async with pool().connection() as connection:
        async with connection.transaction():
            yield connection


async def database_ready() -> bool:
    try:
        async with pool().connection(timeout=2) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                return (await cursor.fetchone()) is not None
    except Exception:
        return False
