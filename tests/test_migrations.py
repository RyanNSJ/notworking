import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from agentdown.db import engine as db
from agentdown.db.schema import metadata

EXPECTED_TABLES = {
    "targets",
    "reports",
    "status_current",
    "status_transitions",
    "salts",
    "usage_daily",
    "checks_hourly",
    "alembic_version",
}


def test_upgrade_creates_schema(migrated_db_url: str) -> None:
    engine = db.make_engine(migrated_db_url)
    try:
        assert set(sa.inspect(engine).get_table_names()) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_migrations_match_schema(migrated_db_url: str) -> None:
    """Fails if schema.py was changed without a matching migration."""
    engine = db.make_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), metadata)
        assert diff == []
    finally:
        engine.dispose()


def test_downgrade_to_base(migrated_db_url: str) -> None:
    db.downgrade(migrated_db_url, "base")
    engine = db.make_engine(migrated_db_url)
    try:
        assert set(sa.inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()


def test_sqlite_uses_wal(migrated_db_url: str) -> None:
    engine = db.make_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("PRAGMA journal_mode")).scalar() == "wal"
    finally:
        engine.dispose()


def test_0002_keeps_listed_flag(db_url: str) -> None:
    """The approved -> listed rename must preserve existing rows."""
    db.upgrade(db_url, "0001")
    engine = db.make_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO targets (type, target_id, approved, created_at) "
                    "VALUES ('site', 'example.com', 1, '2026-10-08 00:00:00')"
                )
            )
        db.upgrade(db_url)
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT target_id, listed FROM targets")).one()
        assert tuple(row) == ("example.com", 1)
    finally:
        engine.dispose()
