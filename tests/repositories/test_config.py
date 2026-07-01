"""Tests for repository-layer config models.

Covers Retrieval, Postgres, Sqlite: pure construction and validation. The
section→field loader mapping for these types lives in tests/config/test_sections.py.
"""

import math

import pytest
from pydantic import ValidationError

from lore.repositories import PostgresConfig, RetrievalConfig, SqliteConfig

# ---------------------------------------------------------------------------
# RetrievalConfig
# ---------------------------------------------------------------------------


def test_retrieval_config_is_frozen() -> None:
    rc = RetrievalConfig(proximity=0.5, authority=0.5, limit=10, fan_out=2, max_keywords=10)
    with pytest.raises(ValidationError, match="frozen"):
        rc.proximity = 0.7  # pyright: ignore[reportAttributeAccessIssue]


def test_retrieval_config_weight_out_of_range_raises() -> None:
    with pytest.raises(ValidationError, match="proximity"):
        RetrievalConfig(proximity=1.5, authority=0.5, limit=10, fan_out=2, max_keywords=10)


def test_retrieval_config_limit_zero_raises() -> None:
    with pytest.raises(ValidationError, match="limit"):
        RetrievalConfig(proximity=0.5, authority=0.5, limit=0, fan_out=2, max_keywords=10)


def test_retrieval_config_max_keywords_zero_raises() -> None:
    with pytest.raises(ValidationError, match="must be > 0"):
        RetrievalConfig(proximity=0.5, authority=0.5, limit=10, fan_out=2, max_keywords=0)


def test_retrieval_config_rejects_weights_not_summing_to_one() -> None:
    with pytest.raises(ValidationError, match=r"sum to 1\.0"):
        RetrievalConfig(proximity=0.7, authority=0.5, limit=10, fan_out=2, max_keywords=10)


def test_retrieval_config_accepts_weights_within_tolerance() -> None:
    exact = RetrievalConfig(proximity=0.5, authority=0.5, limit=10, fan_out=2, max_keywords=10)
    assert exact.proximity + exact.authority == 1.0
    near = RetrievalConfig(proximity=0.6, authority=0.3995, limit=10, fan_out=2, max_keywords=10)
    assert math.isclose(near.proximity + near.authority, 0.9995)


def test_retrieval_config_weights_property_returns_lane_tuple() -> None:
    rc = RetrievalConfig(proximity=0.7, authority=0.3, limit=10, fan_out=2, max_keywords=10)
    assert rc.weights == (0.7, 0.3)


# ---------------------------------------------------------------------------
# PostgresConfig: pool sizing and timeouts
# ---------------------------------------------------------------------------


def test_postgres_config_is_frozen() -> None:
    pc = PostgresConfig(min_size=1, max_size=20, timeout=10.0, max_waiting=50)
    with pytest.raises(ValidationError, match="frozen"):
        pc.max_size = 30  # pyright: ignore[reportAttributeAccessIssue]


def test_postgres_config_max_size_below_min_size_raises() -> None:
    with pytest.raises(ValidationError, match="max_size"):
        PostgresConfig(min_size=10, max_size=5, timeout=10.0, max_waiting=50)


def test_postgres_config_min_size_zero_raises() -> None:
    with pytest.raises(ValidationError, match="min_size"):
        PostgresConfig(min_size=0, max_size=20, timeout=10.0, max_waiting=50)


def test_postgres_config_max_size_zero_raises() -> None:
    with pytest.raises(ValidationError, match="max_size"):
        PostgresConfig(min_size=1, max_size=0, timeout=10.0, max_waiting=50)


def test_postgres_config_uses_upstream_timeout_name() -> None:
    """`timeout` is psycopg_pool's literal kwarg; the value forwards straight through."""
    pc = PostgresConfig(min_size=1, max_size=20, timeout=10.0, max_waiting=50)
    assert pc.timeout == 10.0


def test_postgres_config_rejects_legacy_getconn_timeout_key() -> None:
    """The pre-rename key is gone; extra="forbid" rejects it loudly at load."""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PostgresConfig(
            min_size=1,
            max_size=20,
            getconn_timeout=10.0,  # pyright: ignore[reportCallIssue]
            max_waiting=50,
        )


def test_postgres_config_timeout_zero_raises() -> None:
    with pytest.raises(ValidationError, match="timeout"):
        PostgresConfig(min_size=1, max_size=20, timeout=0.0, max_waiting=50)


def test_postgres_config_timeout_negative_raises() -> None:
    with pytest.raises(ValidationError, match="timeout"):
        PostgresConfig(min_size=1, max_size=20, timeout=-1.0, max_waiting=50)


def test_postgres_config_max_waiting_negative_raises() -> None:
    with pytest.raises(ValidationError, match="max_waiting"):
        PostgresConfig(min_size=1, max_size=20, timeout=10.0, max_waiting=-1)


def test_postgres_config_max_waiting_zero_is_valid_unlimited() -> None:
    """max_waiting=0 is psycopg's unlimited-queue mode; valid but not the default."""
    pc = PostgresConfig(min_size=1, max_size=20, timeout=10.0, max_waiting=0)
    assert pc.max_waiting == 0


def test_postgres_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError, match="extra"):
        PostgresConfig(
            min_size=1,
            max_size=20,
            timeout=10.0,
            max_waiting=50,
            num_workers=4,  # pyright: ignore[reportCallIssue]
        )


@pytest.mark.parametrize("value", ["English", "; DROP TABLE", "naïve", "", "1simple"])
def test_postgres_config_fulltext_config_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValidationError, match="fulltext_config"):
        PostgresConfig(
            min_size=1,
            max_size=20,
            timeout=10.0,
            max_waiting=50,
            fulltext_config=value,
        )


@pytest.mark.parametrize("value", ["english", "german", "french", "simple", "english_stem"])
def test_postgres_config_fulltext_config_accepts_valid_regconfigs(value: str) -> None:
    pc = PostgresConfig(
        min_size=1,
        max_size=20,
        timeout=10.0,
        max_waiting=50,
        fulltext_config=value,
    )
    assert pc.fulltext_config == value


# ---------------------------------------------------------------------------
# SqliteConfig: FTS5 tokenize spec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["Unicode61", "porter'; DROP TABLE --", "naïve", ""])
def test_sqlite_config_fulltext_config_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValidationError, match="fulltext_config"):
        SqliteConfig(fulltext_config=value)


@pytest.mark.parametrize(
    "value", ["unicode61", "porter unicode61", "ascii", "unicode61 remove_diacritics 1"]
)
def test_sqlite_config_fulltext_config_accepts_valid_fts5_specs(value: str) -> None:
    sc = SqliteConfig(fulltext_config=value)
    assert sc.fulltext_config == value
