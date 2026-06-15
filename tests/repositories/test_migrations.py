"""Tests for migration runner and health check utilities.

``run_migrations()`` routing tests mock the backend — we test our routing
logic, not schema application. Smoke tests use real databases.

``check_health()`` tests use real databases — it's our SQL.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import psycopg
import pytest
import structlog

from lore.domain import StorageError
from lore.repositories import PostgresConfig, SqliteConfig, check_health, run_migrations
from lore.repositories.migrate import read_migrations
from tests.repositories.conftest import drop_pg_tables, make_settings

_SQLITE_MIGRATIONS_PACKAGE = "lore.repositories.sqlite.migrations"
_POSTGRES_MIGRATIONS_PACKAGE = "lore.repositories.postgres.migrations"

# --- run_migrations: routing ---


class TestRunMigrationsRouting:
    """run_migrations routes to the correct backend by DSN prefix."""

    @patch("lore.repositories.sqlite.bootstrap.run_migrations")
    def test_run_migrations_sqlite_dsn_routes_to_sqlite(self, mock_sqlite: MagicMock) -> None:
        dsn = "sqlite:///tmp/test.db"
        run_migrations(settings=make_settings(dsn=dsn), embedding_dim=1024)
        mock_sqlite.assert_called_once_with(
            dsn=dsn, embedding_dim=1024, fulltext_config="porter unicode61"
        )

    @patch("lore.repositories.postgres.bootstrap.run_migrations")
    def test_run_migrations_postgresql_dsn_routes_to_postgres(self, mock_pg: MagicMock) -> None:
        dsn = "postgresql://localhost/db"
        run_migrations(settings=make_settings(dsn=dsn), embedding_dim=1024)
        mock_pg.assert_called_once_with(dsn=dsn, embedding_dim=1024, fulltext_config="english")

    @patch("lore.repositories.postgres.bootstrap.run_migrations")
    def test_run_migrations_postgres_scheme_routes_to_postgres(self, mock_pg: MagicMock) -> None:
        dsn = "postgres://localhost/db"
        run_migrations(settings=make_settings(dsn=dsn), embedding_dim=1024)
        mock_pg.assert_called_once_with(dsn=dsn, embedding_dim=1024, fulltext_config="english")

    def test_run_migrations_unsupported_dsn_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported DSN"):
            run_migrations(settings=make_settings(dsn="mysql://localhost/db"), embedding_dim=1024)


# --- run_migrations: SQLite smoke ---


class TestRunMigrationsSqliteBackend:
    """run_migrations applies schema with sqlite-vec loaded."""

    _EXPECTED_TABLES: ClassVar[set[str]] = {
        "hypotheses",
        "attestations",
        "requests",
        "vec_hypotheses",
        "fts_hypotheses",
        "_system",
    }

    def test_run_sqlite_migrations_applies_to_temp_db(self, tmp_path: Path) -> None:
        """Smoke test: migrations apply the SQLite schema."""
        dsn = f"sqlite:///{tmp_path}/test.db"
        run_migrations(settings=make_settings(dsn=dsn), embedding_dim=1024)

        conn = sqlite3.connect(f"{tmp_path}/test.db")
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            table_names = {row[0] for row in rows}
            assert table_names >= self._EXPECTED_TABLES
        finally:
            conn.close()

    def test_run_sqlite_migrations_custom_dim_creates_vec_table_with_that_dimension(
        self, tmp_path: Path
    ) -> None:
        """vec_hypotheses uses the embedding_dim passed to run_migrations."""
        dsn = f"sqlite:///{tmp_path}/test.db"
        run_migrations(settings=make_settings(dsn=dsn), embedding_dim=1536)

        conn = sqlite3.connect(f"{tmp_path}/test.db")
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'vec_hypotheses'"
            ).fetchone()
            assert row is not None, "vec_hypotheses table not found"
            ddl = row[0]
            assert "float[1536]" in ddl
            assert "float[1024]" not in ddl
        finally:
            conn.close()

    def test_run_sqlite_migrations_idempotent(self, tmp_path: Path) -> None:
        """Second call is a no-op."""
        dsn = f"sqlite:///{tmp_path}/test.db"
        settings = make_settings(dsn=dsn)
        run_migrations(settings=settings, embedding_dim=1024)
        run_migrations(settings=settings, embedding_dim=1024)

    def test_run_sqlite_migrations_emits_applied_log_on_fresh_db(self, tmp_path: Path) -> None:
        """Fresh DB: all discovered migrations apply; one ``migrations.applied`` event."""
        dsn = f"sqlite:///{tmp_path}/test.db"
        migrations = read_migrations(_SQLITE_MIGRATIONS_PACKAGE)

        with structlog.testing.capture_logs() as cap:
            run_migrations(settings=make_settings(dsn=dsn), embedding_dim=1024)

        events = [e for e in cap if e.get("event") == "migrations.applied"]
        assert len(events) == 1
        event = events[0]
        assert event["applied"] == len(migrations)
        assert event["skipped"] == 0
        assert event["latest"] == migrations[-1].name

    def test_run_sqlite_migrations_emits_applied_log_on_idempotent_rerun(
        self, tmp_path: Path
    ) -> None:
        """Second call applies nothing; event reports all migrations skipped."""
        dsn = f"sqlite:///{tmp_path}/test.db"
        settings = make_settings(dsn=dsn)
        migrations = read_migrations(_SQLITE_MIGRATIONS_PACKAGE)
        run_migrations(settings=settings, embedding_dim=1024)

        with structlog.testing.capture_logs() as cap:
            run_migrations(settings=settings, embedding_dim=1024)

        events = [e for e in cap if e.get("event") == "migrations.applied"]
        assert len(events) == 1
        event = events[0]
        assert event["applied"] == 0
        assert event["skipped"] == len(migrations)
        assert event["latest"] == migrations[-1].name


# --- run_migrations: PostgreSQL smoke ---


@pytest.fixture
def pg_migrations_dsn(pg_dsn_session: str) -> Iterator[str]:
    """Per-test PostgreSQL DSN with full cleanup for migration tests.

    Drops all tables before the test (clean slate for migration verification),
    then restores schema afterward so the session-scoped DSN is usable by
    subsequent tests. If teardown fails (e.g. connection issue), all subsequent
    PostgreSQL tests fail anyway — no special recovery needed.
    """
    drop_pg_tables(pg_dsn_session)
    yield pg_dsn_session
    drop_pg_tables(pg_dsn_session)
    run_migrations(settings=make_settings(dsn=pg_dsn_session), embedding_dim=1024)


class TestRunMigrationsPostgresBackend:
    """run_migrations applies schema to PostgreSQL."""

    _EXPECTED_TABLES: ClassVar[set[str]] = {
        "hypotheses",
        "attestations",
        "requests",
        "_system",
    }

    def test_run_postgres_migrations_applies_schema(self, pg_migrations_dsn: str) -> None:
        """Smoke test: migrations apply the PostgreSQL schema."""
        run_migrations(settings=make_settings(dsn=pg_migrations_dsn), embedding_dim=1024)

        conn = psycopg.connect(pg_migrations_dsn, autocommit=True)
        try:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
            table_names = {row[0] for row in rows}
            assert table_names >= self._EXPECTED_TABLES
        finally:
            conn.close()

    def test_run_postgres_migrations_idempotent(self, pg_migrations_dsn: str) -> None:
        """Second call is a no-op."""
        settings = make_settings(dsn=pg_migrations_dsn)
        run_migrations(settings=settings, embedding_dim=1024)
        run_migrations(settings=settings, embedding_dim=1024)

    def test_run_postgres_migrations_accepts_postgres_scheme(self, pg_migrations_dsn: str) -> None:
        """DSN with ``postgres://`` scheme applies schema from scratch."""
        dsn = pg_migrations_dsn.replace("postgresql://", "postgres://", 1)
        run_migrations(settings=make_settings(dsn=dsn), embedding_dim=1024)

        conn = psycopg.connect(dsn, autocommit=True)
        try:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
            table_names = {row[0] for row in rows}
            assert table_names >= self._EXPECTED_TABLES
        finally:
            conn.close()

    def test_run_postgres_migrations_custom_dim_creates_vector_with_that_dimension(
        self, pg_migrations_dsn: str
    ) -> None:
        """hypotheses.embedding uses VECTOR(N) matching the embedding_dim argument."""
        run_migrations(settings=make_settings(dsn=pg_migrations_dsn), embedding_dim=1536)

        conn = psycopg.connect(pg_migrations_dsn, autocommit=True)
        try:
            row = conn.execute(
                "SELECT format_type(atttypid, atttypmod) "
                "FROM pg_attribute "
                "WHERE attrelid = 'hypotheses'::regclass AND attname = 'embedding'"
            ).fetchone()
            assert row is not None, "embedding column not found on hypotheses table"
            column_type = row[0]
            assert column_type == "vector(1536)"
        finally:
            conn.close()

    def test_run_postgres_migrations_emits_applied_log_on_fresh_db(
        self, pg_migrations_dsn: str
    ) -> None:
        """Fresh DB: all discovered migrations apply; one ``migrations.applied`` event."""
        migrations = read_migrations(_POSTGRES_MIGRATIONS_PACKAGE)

        with structlog.testing.capture_logs() as cap:
            run_migrations(settings=make_settings(dsn=pg_migrations_dsn), embedding_dim=1024)

        events = [e for e in cap if e.get("event") == "migrations.applied"]
        assert len(events) == 1
        event = events[0]
        assert event["applied"] == len(migrations)
        assert event["skipped"] == 0
        assert event["latest"] == migrations[-1].name

    def test_run_postgres_migrations_emits_applied_log_on_idempotent_rerun(
        self, pg_migrations_dsn: str
    ) -> None:
        """Second call applies nothing; event reports all migrations skipped."""
        settings = make_settings(dsn=pg_migrations_dsn)
        migrations = read_migrations(_POSTGRES_MIGRATIONS_PACKAGE)
        run_migrations(settings=settings, embedding_dim=1024)

        with structlog.testing.capture_logs() as cap:
            run_migrations(settings=settings, embedding_dim=1024)

        events = [e for e in cap if e.get("event") == "migrations.applied"]
        assert len(events) == 1
        event = events[0]
        assert event["applied"] == 0
        assert event["skipped"] == len(migrations)
        assert event["latest"] == migrations[-1].name


# --- check_health: SQLite ---


@pytest.fixture
def sqlite_health_db(tmp_path: Path) -> str:
    """SQLite DB with just the _system table."""
    path = f"{tmp_path}/health.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE _system (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    conn.close()
    return f"sqlite:///{path}"


class TestCheckHealthSqlite:
    """Embedding model drift detection via _system."""

    def test_check_health_no_model_stores_model(self, sqlite_health_db: str) -> None:
        """First call stores the model name."""
        check_health(
            settings=make_settings(dsn=sqlite_health_db, embedding_model="text-embedding-3-small"),
            embedding_dim=1024,
        )

        path = sqlite_health_db.removeprefix("sqlite:///")
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("SELECT value FROM _system WHERE key = 'embedding_model'").fetchone()
            assert row is not None
            assert row[0] == "text-embedding-3-small"
        finally:
            conn.close()

    def test_check_health_same_model_passes(self, sqlite_health_db: str) -> None:
        """Second call with the same model is a no-op."""
        settings = make_settings(dsn=sqlite_health_db, embedding_model="text-embedding-3-small")
        check_health(settings=settings, embedding_dim=1024)
        check_health(settings=settings, embedding_dim=1024)

    def test_check_health_different_model_raises(self, sqlite_health_db: str) -> None:
        """Different model raises StorageError — embedding drift detected."""
        check_health(
            settings=make_settings(dsn=sqlite_health_db, embedding_model="text-embedding-3-small"),
            embedding_dim=1024,
        )

        with pytest.raises(StorageError, match="Embedding model mismatch"):
            check_health(
                settings=make_settings(
                    dsn=sqlite_health_db, embedding_model="text-embedding-3-large"
                ),
                embedding_dim=1024,
            )

    def test_check_health_first_call_with_dim_stores_embedding_dimensions(
        self, sqlite_health_db: str
    ) -> None:
        """First call with embedding_dim stores the dimension in _system."""
        check_health(settings=make_settings(dsn=sqlite_health_db), embedding_dim=1536)

        path = sqlite_health_db.removeprefix("sqlite:///")
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT value FROM _system WHERE key = 'embedding_dimensions'"
            ).fetchone()
            assert row is not None
            assert row[0] == "1536"
        finally:
            conn.close()

    def test_check_health_same_dim_twice_passes(self, sqlite_health_db: str) -> None:
        """Second call with the same embedding_dim is a no-op."""
        settings = make_settings(dsn=sqlite_health_db)
        check_health(settings=settings, embedding_dim=1536)
        check_health(settings=settings, embedding_dim=1536)

    def test_check_health_different_dim_raises(self, sqlite_health_db: str) -> None:
        """Different dimension raises StorageError — embedding dimensions mismatch detected."""
        settings = make_settings(dsn=sqlite_health_db)
        check_health(settings=settings, embedding_dim=1536)

        with pytest.raises(StorageError, match="dimensions mismatch"):
            check_health(settings=settings, embedding_dim=1024)

    def test_check_health_no_system_table_raises_storage_error(self, tmp_path: Path) -> None:
        """check_health without run_migrations raises StorageError."""
        path = f"{tmp_path}/empty.db"
        sqlite3.connect(path).close()
        dsn = f"sqlite:///{path}"

        with pytest.raises(StorageError, match="run_migrations"):
            check_health(
                settings=make_settings(dsn=dsn, embedding_model="text-embedding-3-small"),
                embedding_dim=1024,
            )

    def test_check_health_different_fulltext_config_raises(self, sqlite_health_db: str) -> None:
        """Different fulltext_config raises StorageError — FTS5 tokenizer drift."""
        check_health(
            settings=make_settings(
                dsn=sqlite_health_db, sqlite=SqliteConfig(fulltext_config="porter unicode61")
            ),
            embedding_dim=1024,
        )

        with pytest.raises(StorageError, match="Fulltext config mismatch"):
            check_health(
                settings=make_settings(
                    dsn=sqlite_health_db, sqlite=SqliteConfig(fulltext_config="unicode61")
                ),
                embedding_dim=1024,
            )


# --- check_health: PostgreSQL ---


@pytest.fixture
def pg_health_dsn(pg_dsn_session: str) -> Iterator[str]:
    """PostgreSQL DSN with health-check keys cleared.

    Deletes ``embedding_model``, ``embedding_dimensions``, and ``fulltext_config``
    — migration tracking rows must survive across the session.
    """
    conn = psycopg.connect(pg_dsn_session, autocommit=True)
    try:
        conn.execute(
            "DELETE FROM _system"
            " WHERE key IN ('embedding_model', 'embedding_dimensions', 'fulltext_config')"
        )
    finally:
        conn.close()
    yield pg_dsn_session
    conn = psycopg.connect(pg_dsn_session, autocommit=True)
    try:
        conn.execute(
            "DELETE FROM _system"
            " WHERE key IN ('embedding_model', 'embedding_dimensions', 'fulltext_config')"
        )
    finally:
        conn.close()


class TestCheckHealthPostgres:
    """Embedding model drift detection via _system (PostgreSQL)."""

    def test_check_health_no_model_stores_model(self, pg_health_dsn: str) -> None:
        """First call stores the model name."""
        check_health(
            settings=make_settings(dsn=pg_health_dsn, embedding_model="text-embedding-3-small"),
            embedding_dim=1024,
        )

        conn = psycopg.connect(pg_health_dsn, autocommit=True)
        try:
            row = conn.execute("SELECT value FROM _system WHERE key = 'embedding_model'").fetchone()
            assert row is not None
            assert row[0] == "text-embedding-3-small"
        finally:
            conn.close()

    def test_check_health_same_model_passes(self, pg_health_dsn: str) -> None:
        """Second call with the same model is a no-op."""
        settings = make_settings(dsn=pg_health_dsn, embedding_model="text-embedding-3-small")
        check_health(settings=settings, embedding_dim=1024)
        check_health(settings=settings, embedding_dim=1024)

    def test_check_health_different_model_raises(self, pg_health_dsn: str) -> None:
        """Different model raises StorageError — embedding drift detected."""
        check_health(
            settings=make_settings(dsn=pg_health_dsn, embedding_model="text-embedding-3-small"),
            embedding_dim=1024,
        )

        with pytest.raises(StorageError, match="Embedding model mismatch"):
            check_health(
                settings=make_settings(dsn=pg_health_dsn, embedding_model="text-embedding-3-large"),
                embedding_dim=1024,
            )

    def test_check_health_first_call_with_dim_stores_embedding_dimensions(
        self, pg_health_dsn: str
    ) -> None:
        """First call with embedding_dim stores the dimension in _system."""
        check_health(settings=make_settings(dsn=pg_health_dsn), embedding_dim=1536)

        conn = psycopg.connect(pg_health_dsn, autocommit=True)
        try:
            row = conn.execute(
                "SELECT value FROM _system WHERE key = 'embedding_dimensions'"
            ).fetchone()
            assert row is not None
            assert row[0] == "1536"
        finally:
            conn.close()

    def test_check_health_same_dim_twice_passes(self, pg_health_dsn: str) -> None:
        """Second call with the same embedding_dim is a no-op."""
        settings = make_settings(dsn=pg_health_dsn)
        check_health(settings=settings, embedding_dim=1536)
        check_health(settings=settings, embedding_dim=1536)

    def test_check_health_different_dim_raises(self, pg_health_dsn: str) -> None:
        """Different dimension raises StorageError — embedding dimensions mismatch detected."""
        settings = make_settings(dsn=pg_health_dsn)
        check_health(settings=settings, embedding_dim=1536)

        with pytest.raises(StorageError, match="dimensions mismatch"):
            check_health(settings=settings, embedding_dim=1024)

    def test_check_health_no_system_table_raises_storage_error(self, pg_dsn_session: str) -> None:
        """check_health without run_migrations raises StorageError."""
        # Create a temporary database with no _system table by using a different schema.
        conn = psycopg.connect(pg_dsn_session, autocommit=True)
        try:
            conn.execute("ALTER TABLE _system RENAME TO _system_backup")
        finally:
            conn.close()
        try:
            with pytest.raises(StorageError, match="run_migrations"):
                check_health(
                    settings=make_settings(
                        dsn=pg_dsn_session, embedding_model="text-embedding-3-small"
                    ),
                    embedding_dim=1024,
                )
        finally:
            conn = psycopg.connect(pg_dsn_session, autocommit=True)
            try:
                conn.execute("ALTER TABLE _system_backup RENAME TO _system")
            finally:
                conn.close()

    def test_check_health_different_fulltext_config_raises(self, pg_health_dsn: str) -> None:
        """Different fulltext_config raises StorageError — tsvector lexer drift."""
        english_pg = PostgresConfig(
            min_size=1, max_size=20, getconn_timeout=10.0, max_waiting=50, fulltext_config="english"
        )
        simple_pg = PostgresConfig(
            min_size=1, max_size=20, getconn_timeout=10.0, max_waiting=50, fulltext_config="simple"
        )
        check_health(
            settings=make_settings(dsn=pg_health_dsn, postgres=english_pg), embedding_dim=1024
        )

        with pytest.raises(StorageError, match="Fulltext config mismatch"):
            check_health(
                settings=make_settings(dsn=pg_health_dsn, postgres=simple_pg), embedding_dim=1024
            )


# --- check_health: routing ---


class TestCheckHealthRouting:
    """check_health routes to the correct backend by DSN prefix."""

    @patch("lore.repositories.postgres.bootstrap.check_health")
    def test_check_health_postgres_dsn_routes_to_postgres(self, mock_pg: MagicMock) -> None:
        dsn = "postgresql://localhost/db"
        check_health(settings=make_settings(dsn=dsn, embedding_model="model"), embedding_dim=1024)
        mock_pg.assert_called_once_with(
            dsn=dsn,
            embedding_model="model",
            embedding_dim=1024,
            fulltext_config="english",
        )

    def test_check_health_unsupported_dsn_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported DSN"):
            check_health(settings=make_settings(dsn="mysql://localhost/db"), embedding_dim=1024)
