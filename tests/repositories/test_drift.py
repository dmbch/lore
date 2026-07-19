"""Schema drift guard: asserts structural equivalence between SQLite and PostgreSQL.

Both backends must have the same relational tables with the same columns,
primary keys, indexes, unique constraints, and foreign key constraints. Vector storage is
implementation-specific (virtual table vs column) and excluded from the
comparison.

Requires a running PostgreSQL server (skipped when unavailable).

Uses sync connections directly for schema introspection: this is a
structural test, not a behavioral test of the async Protocol layer.
"""

import re
import sqlite3
from collections.abc import Iterator
from typing import Any, ClassVar

import psycopg
import pytest

# Tables that must exist in both backends with matching columns.
_RELATIONAL_TABLES = ("hypotheses", "attestations", "requests", "_cache")

# Columns expected only in one backend (implementation-specific).
# - embedding: pgvector VECTOR column (SQLite uses vec_hypotheses virtual table)
# - fulltext: generated tsvector column (SQLite uses fts_hypotheses virtual table)
_PG_ONLY_COLUMNS: dict[str, set[str]] = {
    "hypotheses": {"embedding", "fulltext"},
}

# Strict 1:1 type mapping: SQLite type → PostgreSQL type.
# SQLite INTEGER is 64-bit; PostgreSQL equivalent is BIGINT.
_TYPE_MAP: dict[str, str] = {
    "TEXT": "text",
    "INTEGER": "bigint",
    "REAL": "double precision",
}

# Columns where PostgreSQL uses a different type than the standard mapping.
# Hypothesis-side IDs are UUIDv4-minted by ``generate_id()`` and use native UUID
# on Postgres for storage efficiency. ``requests.id`` / ``attestations.correlation_id``
# carry the FastMCP-tool-call ``trace_id.span_id`` composite (or a uuid4 hex
# fallback under bare runs). Both are plain strings, not UUIDs, so the column
# type is TEXT on both backends.
_PG_TYPE_OVERRIDES: dict[tuple[str, str], str] = {
    ("hypotheses", "id"): "uuid",
    ("attestations", "id"): "uuid",
    ("attestations", "hypothesis_id"): "uuid",
}


# --- Column introspection ---
def _sqlite_columns(
    conn: sqlite3.Connection, table: str
) -> dict[str, tuple[str, bool, str | None]]:
    """Return {column_name: (type, notnull, default)} from PRAGMA table_info."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1]: (row[2], bool(row[3]), row[4]) for row in rows}


def _pg_columns(
    conn: psycopg.Connection[Any], table: str
) -> dict[str, tuple[str, bool, str | None]]:
    """Return {column_name: (data_type, notnull, default)} from information_schema.columns."""
    rows = conn.execute(
        "SELECT column_name, data_type, is_nullable, column_default"
        " FROM information_schema.columns"
        " WHERE table_name = %s ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return {row[0]: (row[1], row[2] == "NO", row[3]) for row in rows}


# --- Primary key introspection ---
def _sqlite_pks(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return {column_name} for primary key columns from PRAGMA table_info."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows if row[5]}


def _pg_pks(conn: psycopg.Connection[Any], table: str) -> set[str]:
    """Return {column_name} for primary key columns from pg_index."""
    rows = conn.execute(
        "SELECT a.attname"
        " FROM pg_index ix"
        " JOIN pg_class t ON t.oid = ix.indrelid"
        " JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)"
        " WHERE t.relname = %s AND ix.indisprimary",
        (table,),
    ).fetchall()
    return {row[0] for row in rows}


# --- Index introspection ---
def _sqlite_indexes(conn: sqlite3.Connection, table: str) -> dict[str, list[str]]:
    """Return {index_name: [columns]} for explicit CREATE INDEX indexes.

    Filters by origin='c' to exclude auto-indexes from PRIMARY KEY and UNIQUE.
    """
    indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
    result: dict[str, list[str]] = {}
    for idx in indexes:
        name, origin = idx[1], idx[3]
        if origin != "c":
            continue
        cols = conn.execute(f"PRAGMA index_info({name})").fetchall()
        result[name] = [col[2] for col in cols]
    return result


def _pg_indexes(conn: psycopg.Connection[Any], table: str) -> dict[str, list[str]]:
    """Return {index_name: [columns]} for non-constraint indexes.

    Excludes indexes that back PRIMARY KEY or UNIQUE constraints: those are
    covered by the column-level checks.
    """
    rows = conn.execute(
        "SELECT i.relname, a.attname"
        " FROM pg_index ix"
        " JOIN pg_class t ON t.oid = ix.indrelid"
        " JOIN pg_class i ON i.oid = ix.indexrelid"
        " JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)"
        " WHERE t.relname = %s AND NOT ix.indisprimary AND NOT ix.indisunique"
        " ORDER BY i.relname, array_position(ix.indkey, a.attnum)",
        (table,),
    ).fetchall()
    result: dict[str, list[str]] = {}
    for name, col in rows:
        result.setdefault(name, []).append(col)
    return result


def _pg_index_methods(conn: psycopg.Connection[Any], table: str) -> dict[str, str]:
    """Return {index_name: access_method} for non-constraint indexes."""
    rows = conn.execute(
        "SELECT i.relname, am.amname"
        " FROM pg_index ix"
        " JOIN pg_class t ON t.oid = ix.indrelid"
        " JOIN pg_class i ON i.oid = ix.indexrelid"
        " JOIN pg_am am ON am.oid = i.relam"
        " WHERE t.relname = %s AND NOT ix.indisprimary AND NOT ix.indisunique",
        (table,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _sqlite_index_partiality(conn: sqlite3.Connection, table: str) -> dict[str, bool]:
    """Return {index_name: is_partial} for explicit CREATE INDEX indexes.

    PRAGMA index_list columns: seq, name, unique, origin, partial.
    """
    indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return {idx[1]: bool(idx[4]) for idx in indexes if idx[3] == "c"}


def _pg_index_partiality(conn: psycopg.Connection[Any], table: str) -> dict[str, bool]:
    """Return {index_name: is_partial} for non-constraint indexes."""
    rows = conn.execute(
        "SELECT i.relname, ix.indpred IS NOT NULL"
        " FROM pg_index ix"
        " JOIN pg_class t ON t.oid = ix.indrelid"
        " JOIN pg_class i ON i.oid = ix.indexrelid"
        " WHERE t.relname = %s AND NOT ix.indisprimary AND NOT ix.indisunique",
        (table,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


# --- Foreign key introspection ---
def _sqlite_fks(conn: sqlite3.Connection, table: str) -> set[tuple[str, str, str, str, str]]:
    """Return {(from_col, to_table, to_col, on_delete, on_update)} from PRAGMA foreign_key_list."""
    rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    # PRAGMA foreign_key_list columns: id, seq, table, from, to, on_update, on_delete, match.
    return {(row[3], row[2], row[4], row[6], row[5]) for row in rows}


def _pg_fks(conn: psycopg.Connection[Any], table: str) -> set[tuple[str, str, str, str, str]]:
    """Return {(from_col, to_table, to_col, on_delete, on_update)} from information_schema."""
    rows = conn.execute(
        "SELECT kcu.column_name, ccu.table_name, ccu.column_name,"
        "       rc.delete_rule, rc.update_rule"
        " FROM information_schema.table_constraints tc"
        " JOIN information_schema.key_column_usage kcu"
        "   ON tc.constraint_name = kcu.constraint_name"
        "   AND tc.table_schema = kcu.table_schema"
        " JOIN information_schema.constraint_column_usage ccu"
        "   ON ccu.constraint_name = tc.constraint_name"
        "   AND ccu.table_schema = tc.table_schema"
        " JOIN information_schema.referential_constraints rc"
        "   ON rc.constraint_name = tc.constraint_name"
        "   AND rc.constraint_schema = tc.table_schema"
        " WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = %s",
        (table,),
    ).fetchall()
    return {(row[0], row[1], row[2], row[3], row[4]) for row in rows}


# --- Unique constraint introspection ---
def _sqlite_uniques(conn: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    """Return {(col, ...)} for each UNIQUE constraint (excluding PRIMARY KEY).

    SQLite auto-creates indexes for UNIQUE constraints with origin='u'.
    """
    indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
    result: set[tuple[str, ...]] = set()
    for idx in indexes:
        unique, origin = bool(idx[2]), idx[3]
        if unique and origin == "u":
            cols = conn.execute(f"PRAGMA index_info({idx[1]})").fetchall()
            result.add(tuple(col[2] for col in cols))
    return result


def _pg_uniques(conn: psycopg.Connection[Any], table: str) -> set[tuple[str, ...]]:
    """Return {(col, ...)} for each UNIQUE constraint (excluding PRIMARY KEY).

    Uses pg_index to find unique, non-primary indexes and their columns.
    """
    rows = conn.execute(
        "SELECT i.relname, a.attname"
        " FROM pg_index ix"
        " JOIN pg_class t ON t.oid = ix.indrelid"
        " JOIN pg_class i ON i.oid = ix.indexrelid"
        " JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)"
        " WHERE t.relname = %s AND ix.indisunique AND NOT ix.indisprimary"
        " ORDER BY i.relname, array_position(ix.indkey, a.attnum)",
        (table,),
    ).fetchall()
    by_index: dict[str, list[str]] = {}
    for name, col in rows:
        by_index.setdefault(name, []).append(col)
    return {tuple(cols) for cols in by_index.values()}


# --- CHECK constraint introspection ---
# We compare the columns covered by CHECK constraints AND the bounds:
# both backends are expected to enforce the same numeric ranges per
# column. SQLite renders ``CHECK (col BETWEEN x AND y)`` literally;
# PostgreSQL normalises to ``CHECK ((col >= x) AND (col <= y))`` in
# ``pg_get_constraintdef()`` output. Both forms are parsed into
# ``(column, lower, upper)`` triples for comparison.

# Columns guarded by CHECK constraints in the schema. Trust-related
# columns carry two-sided ``BETWEEN`` bounds; ``n_oracle_prior`` carries
# a one-sided ``>= 0`` guard. Enumeration is exhaustive.
_CHECK_CANDIDATE_COLUMNS = (
    "c_oracle_raw",
    "c_oracle_discounted",
    "c_herd",
    "t_oracle",
    "n_oracle_prior",
)

# Numeric literal as PostgreSQL or SQLite might render it: optional sign,
# optional quotes, optional surrounding parentheses, optional ``::type``
# cast. Matches ``-1.0``, ``(-1.0)``, ``'-1'::numeric``, ``(-1)::double
# precision``, etc.
_NUM_PATTERN = (
    r"\(?\s*'?(?P<{name}>-?\d+(?:\.\d+)?)'?\s*\)?"
    r"(?:\s*::\s*[a-z_][a-z_0-9 ]*)?"
)
# Column reference as PG might render it: optional surrounding parens,
# optional ``::type`` cast suffix.
_COL_REF_PATTERN = (
    r"\(?(?P<{name}>" + "|".join(_CHECK_CANDIDATE_COLUMNS) + r")\)?"
    r"(?:\s*::\s*[a-z_][a-z_0-9 ]*)?"
)
_BETWEEN_RE = re.compile(
    _COL_REF_PATTERN.format(name="col")
    + r"\s+between\s+"
    + _NUM_PATTERN.format(name="lower")
    + r"\s+and\s+"
    + _NUM_PATTERN.format(name="upper"),
    re.IGNORECASE,
)
# The two-sided range form references the column twice; avoid named-group
# duplication by anchoring the second mention via backreference to the
# first capture's literal value (built per-column inside the helper).
_RANGE_LHS_RE = re.compile(
    _COL_REF_PATTERN.format(name="col") + r"\s*>=\s*" + _NUM_PATTERN.format(name="lower"),
    re.IGNORECASE,
)
_RANGE_RHS_RE = re.compile(
    _COL_REF_PATTERN.format(name="col") + r"\s*<=\s*" + _NUM_PATTERN.format(name="upper"),
    re.IGNORECASE,
)


def _parse_bounds(text: str) -> set[tuple[str, float, float]]:
    """Extract ``(column, lower, upper)`` triples from CHECK clause text.

    Tries three renderings: ``col BETWEEN x AND y`` (SQLite two-sided),
    ``col >= x ... col <= y`` (PostgreSQL normalised two-sided), and the
    one-sided forms ``col >= x`` or ``col <= y``. One-sided constraints
    fill the missing side with ``±inf`` so set-equality across backends
    still catches asymmetric drift; ``±inf`` is a sentinel meaning "no
    constraint on this side," not a real bound. Tolerates the
    quoted-numeric / type-cast / parenthesised-column forms PG sometimes
    injects (``'-1'::numeric``, ``(-1.0)``, ``(c_oracle_raw)::double
    precision``).

    Assumes at most one ``>=`` and one ``<=`` per column per CHECK
    clause text; multiple range constraints on the same column would be
    last-write-wins. The drift guard's cross-backend set-equality still
    catches divergence, just not duplicate-constraint shapes that happen
    to agree on both backends.
    """
    triples: set[tuple[str, float, float]] = set()
    for match in _BETWEEN_RE.finditer(text):
        triples.add(
            (match.group("col").lower(), float(match.group("lower")), float(match.group("upper")))
        )
    # The range form needs per-column pairing because the column
    # reference appears once with ``>=`` and once with ``<=`` for
    # two-sided constraints, or only on one side for single-sided ones.
    # A single regex with a backreference cannot tolerate the optional
    # cast/paren shapes between the two halves.
    lower_by_col: dict[str, float] = {}
    upper_by_col: dict[str, float] = {}
    for match in _RANGE_LHS_RE.finditer(text):
        lower_by_col[match.group("col").lower()] = float(match.group("lower"))
    for match in _RANGE_RHS_RE.finditer(text):
        upper_by_col[match.group("col").lower()] = float(match.group("upper"))
    for col in lower_by_col.keys() | upper_by_col.keys():
        triples.add(
            (col, lower_by_col.get(col, float("-inf")), upper_by_col.get(col, float("inf")))
        )
    return triples


def _sqlite_check_bounds(conn: sqlite3.Connection, table: str) -> set[tuple[str, float, float]]:
    """Return ``(column, lower, upper)`` triples from CHECK constraints.

    SQLite stores the original CREATE TABLE statement in ``sqlite_master``;
    parse the bounds out of the CHECK clauses.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None or row[0] is None:
        return set()
    return _parse_bounds(row[0])


def _pg_check_bounds(conn: psycopg.Connection[Any], table: str) -> set[tuple[str, float, float]]:
    """Return ``(column, lower, upper)`` triples from CHECK constraints.

    Uses ``pg_get_constraintdef()`` (the canonical source text PostgreSQL
    renders for a CHECK clause). Excludes NOT NULL CHECKs by name pattern.
    """
    rows = conn.execute(
        "SELECT pg_get_constraintdef(c.oid)"
        " FROM pg_constraint c"
        " JOIN pg_class t ON t.oid = c.conrelid"
        " WHERE t.relname = %s"
        "   AND c.contype = 'c'"
        "   AND c.conname NOT LIKE '%%_not_null'",
        (table,),
    ).fetchall()
    triples: set[tuple[str, float, float]] = set()
    for row in rows:
        triples |= _parse_bounds(row[0])
    return triples


@pytest.fixture
def drift_conns(
    sqlite_dsn_session: str,
    pg_dsn: str,
) -> Iterator[tuple[sqlite3.Connection, psycopg.Connection[Any]]]:
    """Sync connections for schema comparison. Migrations already applied by session fixtures."""
    sqlite_path = sqlite_dsn_session.removeprefix("sqlite:///")
    sq = sqlite3.connect(sqlite_path)

    pg = psycopg.connect(pg_dsn, autocommit=True)

    yield sq, pg

    sq.close()
    pg.close()


class TestSchemaDrift:
    """SQLite and PostgreSQL schemas must be structurally equivalent."""

    @pytest.mark.parametrize("table", _RELATIONAL_TABLES)
    def test_same_tables_same_columns(
        self, drift_conns: tuple[sqlite3.Connection, psycopg.Connection[Any]], table: str
    ) -> None:
        sq, pg = drift_conns
        sq_cols = _sqlite_columns(sq, table)
        pg_cols = _pg_columns(pg, table)

        # Existence anchor: a table absent from both backends would pass
        # every structural comparison as vacuously equal empty structures.
        assert sq_cols, f"Table {table} missing in SQLite"

        # Remove backend-specific columns before comparison.
        pg_only = _PG_ONLY_COLUMNS.get(table, set())
        pg_shared = {k: v for k, v in pg_cols.items() if k not in pg_only}

        # Same column names in the same order.
        assert list(sq_cols.keys()) == list(pg_shared.keys()), (
            f"Column mismatch in {table}: "
            f"SQLite={list(sq_cols.keys())}, "
            f"PostgreSQL={list(pg_shared.keys())}"
        )

        # Compatible types and matching nullability for each shared column.
        for col_name, (sq_type, sq_notnull, sq_default) in sq_cols.items():
            pg_type, pg_notnull, pg_default = pg_shared[col_name]

            # Check type: use override if present, otherwise standard mapping.
            expected_pg = _PG_TYPE_OVERRIDES.get((table, col_name))
            if expected_pg is None:
                expected_pg = _TYPE_MAP.get(sq_type)
                assert expected_pg is not None, (
                    f"Unmapped SQLite type {sq_type!r} for {table}.{col_name}"
                )
            assert pg_type == expected_pg, (
                f"Type mismatch for {table}.{col_name}: "
                f"SQLite {sq_type!r} → expected PG {expected_pg!r}, got {pg_type!r}"
            )

            # Check NOT NULL parity.
            assert sq_notnull == pg_notnull, (
                f"NOT NULL mismatch for {table}.{col_name}: "
                f"SQLite notnull={sq_notnull}, PostgreSQL notnull={pg_notnull}"
            )

            # Defaults render differently across backends; assert presence
            # parity, not text equality.
            assert (sq_default is None) == (pg_default is None), (
                f"Column default presence mismatch for {table}.{col_name}: "
                f"SQLite default={sq_default!r}, PostgreSQL default={pg_default!r}"
            )

    @pytest.mark.parametrize("table", _RELATIONAL_TABLES)
    def test_same_primary_keys(
        self, drift_conns: tuple[sqlite3.Connection, psycopg.Connection[Any]], table: str
    ) -> None:
        sq, pg = drift_conns
        sq_pks = _sqlite_pks(sq, table)
        pg_pks_set = _pg_pks(pg, table)

        assert sq_pks == pg_pks_set, (
            f"Primary key mismatch in {table}: "
            f"SQLite={sorted(sq_pks)}, "
            f"PostgreSQL={sorted(pg_pks_set)}"
        )

    # PostgreSQL-only indexes (no SQLite equivalent).
    # - idx_hypotheses_fulltext: GIN index on generated tsvector column.
    # - hypotheses_embedding_hnsw: pgvector HNSW index on the embedding
    #   column. sqlite-vec has no index analogue and brute-forces
    #   proximity queries.
    _PG_ONLY_INDEXES: ClassVar[set[str]] = {"idx_hypotheses_fulltext", "hypotheses_embedding_hnsw"}

    @pytest.mark.parametrize("table", _RELATIONAL_TABLES)
    def test_same_indexes(
        self, drift_conns: tuple[sqlite3.Connection, psycopg.Connection[Any]], table: str
    ) -> None:
        sq, pg = drift_conns
        sq_idx = _sqlite_indexes(sq, table)
        pg_idx = {k: v for k, v in _pg_indexes(pg, table).items() if k not in self._PG_ONLY_INDEXES}

        assert set(sq_idx.keys()) == set(pg_idx.keys()), (
            f"Index name mismatch in {table}: "
            f"SQLite={sorted(sq_idx.keys())}, "
            f"PostgreSQL={sorted(pg_idx.keys())}"
        )

        for idx_name in sq_idx:
            assert sq_idx[idx_name] == pg_idx[idx_name], (
                f"Index column mismatch for {table}.{idx_name}: "
                f"SQLite={sq_idx[idx_name]}, "
                f"PostgreSQL={pg_idx[idx_name]}"
            )

    @pytest.mark.parametrize("table", _RELATIONAL_TABLES)
    def test_same_index_partiality(
        self, drift_conns: tuple[sqlite3.Connection, psycopg.Connection[Any]], table: str
    ) -> None:
        """A partial index on one backend and a full one on the other would
        pass the name and column checks while covering different row sets
        (idx_cache_expires_at excludes NULL-expiry rows on both). Predicate
        *text* is not compared: each backend renders it differently, and
        the flag catches the drift that matters.
        """
        sq, pg = drift_conns
        sq_partial = _sqlite_index_partiality(sq, table)
        pg_partial = {
            k: v
            for k, v in _pg_index_partiality(pg, table).items()
            if k not in self._PG_ONLY_INDEXES
        }

        assert sq_partial == pg_partial, (
            f"Index partiality mismatch in {table}: SQLite={sq_partial}, PostgreSQL={pg_partial}"
        )

    @pytest.mark.parametrize("table", _RELATIONAL_TABLES)
    def test_shared_indexes_use_btree(
        self, drift_conns: tuple[sqlite3.Connection, psycopg.Connection[Any]], table: str
    ) -> None:
        """Shared indexes must use btree on Postgres: SQLite has no other access method."""
        _, pg = drift_conns
        shared_methods = {
            name: method
            for name, method in _pg_index_methods(pg, table).items()
            if name not in self._PG_ONLY_INDEXES
        }

        assert all(m == "btree" for m in shared_methods.values()), (
            f"Non-btree access method on shared index in {table}: {shared_methods}"
        )

    @pytest.mark.parametrize("table", _RELATIONAL_TABLES)
    def test_same_foreign_keys(
        self, drift_conns: tuple[sqlite3.Connection, psycopg.Connection[Any]], table: str
    ) -> None:
        sq, pg = drift_conns
        sq_fks = _sqlite_fks(sq, table)
        pg_fks_set = _pg_fks(pg, table)

        assert sq_fks == pg_fks_set, (
            f"Foreign key mismatch in {table}: "
            f"SQLite={sorted(sq_fks)}, "
            f"PostgreSQL={sorted(pg_fks_set)}"
        )

    @pytest.mark.parametrize("table", _RELATIONAL_TABLES)
    def test_same_unique_constraints(
        self, drift_conns: tuple[sqlite3.Connection, psycopg.Connection[Any]], table: str
    ) -> None:
        sq, pg = drift_conns
        sq_uq = _sqlite_uniques(sq, table)
        pg_uq = _pg_uniques(pg, table)

        assert sq_uq == pg_uq, (
            f"UNIQUE constraint mismatch in {table}: "
            f"SQLite={sorted(sq_uq)}, "
            f"PostgreSQL={sorted(pg_uq)}"
        )

    @pytest.mark.parametrize("table", _RELATIONAL_TABLES)
    def test_same_check_constraint_bounds(
        self, drift_conns: tuple[sqlite3.Connection, psycopg.Connection[Any]], table: str
    ) -> None:
        """Both backends must guard the same columns with the same numeric bounds.

        Compares ``(column, lower, upper)`` triples across backends. A
        future drift like ``CHECK (c_oracle_raw BETWEEN -1.0 AND 2.0)``
        on one backend would fail this assertion: the column-set check
        alone (the previous form) treated divergent bounds as equivalent.
        One-sided constraints (e.g. ``n_oracle_prior >= 0``) yield triples
        with ``+inf`` or ``-inf`` on the missing side, so asymmetric drift
        is caught with the same set-equality check.
        """
        sq, pg = drift_conns
        sq_bounds = _sqlite_check_bounds(sq, table)
        pg_bounds = _pg_check_bounds(pg, table)

        assert sq_bounds == pg_bounds, (
            f"CHECK constraint bounds mismatch in {table} "
            f"(±inf indicates an unconstrained side, not a real bound): "
            f"SQLite={sorted(sq_bounds)}, "
            f"PostgreSQL={sorted(pg_bounds)}"
        )


class TestParseBounds:
    """The CHECK-clause parser handles two-sided and one-sided forms.

    Unit tests on ``_parse_bounds`` directly so the drift-guard regex
    has explicit coverage independent of the parametrised cross-backend
    comparison above.
    """

    def test_between_form_yields_two_sided_triple(self) -> None:
        assert _parse_bounds("CHECK (c_oracle_raw BETWEEN -1.0 AND 1.0)") == {
            ("c_oracle_raw", -1.0, 1.0)
        }

    def test_range_form_yields_two_sided_triple(self) -> None:
        assert _parse_bounds("CHECK ((c_herd >= -1.0) AND (c_herd <= 1.0))") == {
            ("c_herd", -1.0, 1.0)
        }

    def test_one_sided_lower_yields_open_upper(self) -> None:
        assert _parse_bounds("CHECK (n_oracle_prior >= 0)") == {
            ("n_oracle_prior", 0.0, float("inf"))
        }

    def test_one_sided_upper_yields_open_lower(self) -> None:
        assert _parse_bounds("CHECK (n_oracle_prior <= 100)") == {
            ("n_oracle_prior", float("-inf"), 100.0)
        }

    def test_pg_normalised_form_with_casts_yields_same_triple(self) -> None:
        """PostgreSQL's ``pg_get_constraintdef`` renders bounds with casts
        and parentheses (``'-1'::numeric``, ``(c_oracle_raw)::double precision``).
        The parser must produce the same triple as the SQLite-native form.
        """
        assert _parse_bounds(
            "CHECK (((c_oracle_raw)::numeric >= '-1.0'::numeric)"
            " AND ((c_oracle_raw)::numeric <= '1.0'::numeric))"
        ) == {("c_oracle_raw", -1.0, 1.0)}
