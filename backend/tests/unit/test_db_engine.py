"""D1: DB engine pool tuning. SQLite ignores pool args; Postgres gets the tuned
QueuePool by default, or NullPool when fronted by PgBouncer."""
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db import _build_engine


def test_sqlite_ignores_pool_args():
    # SQLite is single-writer/file-based — passing pool_size would be wrong, so it's skipped.
    eng = _build_engine("sqlite+aiosqlite:///:memory:", Settings())
    assert eng.dialect.name == "sqlite"


def test_postgres_uses_tuned_queue_pool():
    s = Settings(database_url="postgresql+asyncpg://u:p@h/db", db_pool_size=7, db_max_overflow=20)
    eng = _build_engine(s.database_url, s)
    assert eng.pool.size() == 7
    assert eng.pool._max_overflow == 20


def test_postgres_null_pool_for_pgbouncer():
    s = Settings(database_url="postgresql+asyncpg://u:p@h/db", db_use_null_pool=True)
    eng = _build_engine(s.database_url, s)
    assert isinstance(eng.pool, NullPool)
