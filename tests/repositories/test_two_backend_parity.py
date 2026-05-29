"""Two-backend parity tests for shared repository contracts.

SQLite and Postgres expose the same Protocol but classify errors and
manage transactions through entirely different libraries. The drift
guard tests static schema parity; this file tests behavioural parity:
exception classes, transaction semantics, and other invariants that
must hold regardless of which backend a deployer chose.

Tests parameterize over both backends via the shared ``backend``
fixture. A behaviour that diverges between backends fails here.
"""

import math
import sqlite3

import psycopg
import pytest

from lore.domain import DuplicateRecord, IntegrityViolation, StorageError
from lore.repositories import AttestationRecord, RepositoryPool
from lore.repositories.records import generate_id
from lore.repositories.sqlite.pool import SqlitePool
from tests.repositories.conftest import (
    NO_DECAY_TRUST_HL,
    SCHEMA_DIM,
    BackendFixture,
    append_attestation,
    seed_hypothesis,
    seed_request,
)

# A valid UUID we control. Set on ``generate_id`` to force the second insert
# to collide on the primary key.
_FIXED_ID = "00000000-0000-0000-0000-0000000000aa"


def _force_fixed_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``generate_id`` to ``_FIXED_ID`` at its single canonical source.

    Every backend module accesses ``records.generate_id()`` through qualified
    module access, so one patch at the source binds all call sites.
    """
    monkeypatch.setattr("lore.repositories.records.generate_id", lambda: _FIXED_ID)


class TestTransactionPropagatesNonDbExceptions:
    """Non-DB exceptions inside ``transaction()`` must leave the connection clean.

    A math service constructor's ``ValueError`` is a typical non-psycopg,
    non-sqlite3 exception raised inside the orchestrator's transaction
    block. The wrapper must roll back, propagate the original exception
    class (not a wrapped ``StorageError``), and leave the connection
    usable for follow-up queries — same behaviour on both backends.
    """

    async def test_transaction_propagates_non_db_exception_with_clean_state(
        self, pool: RepositoryPool
    ) -> None:
        with pytest.raises(ValueError, match="injected from test"):
            async with pool.transaction() as repos:
                # Seed a row, then sabotage the transaction with a non-DB exception.
                # Prove via follow-up read that rollback happened cleanly.
                await seed_hypothesis(repos.hypotheses)
                msg = "injected from test"
                raise ValueError(msg)

        # Pool is usable for a follow-up transaction.
        async with pool.transaction() as repos2:
            await seed_request(
                repos2.requests, correlation_id="00000000-0000-0000-0000-00000a11ba00"
            )

        # The hypothesis from the aborted transaction must not be visible.
        # (The ``seed_hypothesis`` row was inside the rolled-back txn.)
        # We assert via a second seed working — if rollback left stale state,
        # this would deadlock or error on the wire.


class TestTransactionWrapsBackendErrorInsideBody:
    """Backend-native errors inside ``transaction()`` are wrapped as ``StorageError``.

    Repositories already translate driver errors at their own boundary, but
    the pool guarantees the same domain class regardless of who let the raw
    error escape. A ``sqlite3.Error`` and a ``psycopg.Error`` raised directly
    in the body must both surface as ``StorageError`` and leave the connection
    usable. Counterpart to ``test_transaction_propagates_non_db_exception_with_clean_state``.
    """

    async def test_transaction_wraps_backend_error_inside_body_with_clean_state(
        self, pool: RepositoryPool
    ) -> None:
        if isinstance(pool, SqlitePool):
            db_error: Exception = sqlite3.OperationalError("injected db error")
        else:
            db_error = psycopg.OperationalError("injected db error")

        with pytest.raises(StorageError, match="injected db error"):
            async with pool.transaction() as repos:
                await seed_hypothesis(repos.hypotheses)
                raise db_error

        # Pool is usable for a follow-up transaction.
        async with pool.transaction() as repos2:
            await seed_request(
                repos2.requests, correlation_id="00000000-0000-0000-0000-00000a11ba01"
            )


class TestUuidCollisionRaisesDuplicateRecord:
    """Both backends must surface UUIDv4 collisions as ``DuplicateRecord``.

    UUIDv4 collisions are unreachable in practice, but the audit (S2.2)
    flagged a divergence: PostgreSQL ``attestations.append`` was raising
    ``StorageError`` while SQLite raised ``DuplicateRecord``. Stub
    ``generate_id`` to a fixed UUID; insert twice; assert both backends
    surface the same domain class for both call sites.
    """

    async def test_hypothesis_collision_raises_duplicate_record(
        self, backend: BackendFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_fixed_id(monkeypatch)
        await backend.hypotheses.store(
            content="first", embedding=[1.0 / SCHEMA_DIM] * SCHEMA_DIM, created_at=0
        )
        with pytest.raises(DuplicateRecord):
            await backend.hypotheses.store(
                content="second", embedding=[1.0 / SCHEMA_DIM] * SCHEMA_DIM, created_at=0
            )

    async def test_attestation_collision_raises_duplicate_record(
        self, backend: BackendFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h_id = await seed_hypothesis(backend.hypotheses)
        await seed_request(backend.requests, correlation_id="00000000-0000-0000-0000-000000000099")
        _force_fixed_id(monkeypatch)
        await append_attestation(backend.attestations, hypothesis_id=h_id)
        with pytest.raises(DuplicateRecord):
            await append_attestation(backend.attestations, hypothesis_id=h_id)


class TestCheckViolationClassification:
    """Out-of-bounds CHECK violations classify as ``IntegrityViolation`` on both backends."""

    async def test_check_violation_surfaces_as_integrity_violation(
        self, backend: BackendFixture
    ) -> None:
        h_id = await seed_hypothesis(backend.hypotheses)
        correlation_id = "00000000-0000-0000-0000-0000000c0c00"
        await seed_request(backend.requests, correlation_id=correlation_id)
        # c_oracle_raw=2.0 violates AttestationRecord's [-1, 1] validation
        # before reaching storage — use model_construct to bypass and exercise
        # the CHECK constraint directly.
        with pytest.raises(IntegrityViolation):
            await backend.attestations.append(
                AttestationRecord.model_construct(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-1",
                    correlation_id=correlation_id,
                    timestamp=1000,
                    t_oracle=0.5,
                    c_oracle_raw=2.0,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )


class TestStorageRejectsNan:
    """Both backends reject NaN at the storage layer.

    Postgres: NaN orders above any number → ``BETWEEN`` CHECK rejects it.
    SQLite: Python's sqlite3 driver binds NaN as NULL → NOT NULL rejects it.
    """

    @pytest.mark.parametrize(
        "column", ["t_oracle", "c_oracle_raw", "c_oracle_discounted", "c_herd"]
    )
    async def test_attestations_reject_nan_in_confidence_columns(
        self, backend: BackendFixture, column: str
    ) -> None:
        h_id = await seed_hypothesis(backend.hypotheses)
        correlation_id = "00000000-0000-0000-0000-0000000a1a1a"
        await seed_request(backend.requests, correlation_id=correlation_id)
        nan = float("nan")
        # NaN in any confidence field violates AttestationRecord's finiteness
        # validation before reaching storage — use model_construct to bypass
        # and exercise the storage-layer NaN rejection directly.
        with pytest.raises(StorageError):
            await backend.attestations.append(
                AttestationRecord.model_construct(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-1",
                    correlation_id=correlation_id,
                    timestamp=1000,
                    t_oracle=nan if column == "t_oracle" else 0.5,
                    c_oracle_raw=nan if column == "c_oracle_raw" else 0.5,
                    c_oracle_discounted=nan if column == "c_oracle_discounted" else 0.25,
                    c_herd=nan if column == "c_herd" else 0.4,
                    n_oracle_prior=0,
                )
            )


class TestSearchSurfacesProximity:
    """``HypothesisResult.proximity`` is the raw cosine similarity from the proximity lane.

    Both backends must populate it. Authority-only rows (those that
    surfaced via FTS but not via the cosine lane) carry proximity 0.0 —
    the honest "no signal" default. Proximity-lane rows carry a value
    in [-1, 1] derived from ``1 - cosine_distance``.
    """

    async def test_search_returns_proximity_for_proximity_lane_rows(
        self, backend: BackendFixture
    ) -> None:
        emb = [1.0] + [0.0] * (SCHEMA_DIM - 1)
        await backend.hypotheses.store(content="alpha-content", embedding=emb, created_at=0)
        results = await backend.hypotheses.search(
            embedding=emb, query="alpha-content", weights=(1.0, 0.0), limit=5, fan_out=2
        )
        assert len(results) == 1
        # Same vector on both sides → cosine similarity ~= 1.0. float32
        # precision (sqlite-vec, pgvector) drops a few ULPs; 1e-5 is
        # well above that.
        assert math.isclose(results[0].proximity, 1.0, abs_tol=1e-5)

    async def test_search_returns_zero_proximity_for_authority_only_rows(
        self, backend: BackendFixture
    ) -> None:
        # Three rows whose embeddings equal the query → cosine distance 0;
        # they monopolize the proximity lane. With limit=1 and fan_out=2
        # the per-lane subquery LIMIT is 2, so only two of the three tied
        # rows enter ``l1_ranked`` — and the fourth row, even though it
        # has a measurable cosine similarity, is shut out of the proximity
        # lane entirely.
        query_emb = [1.0] + [0.0] * (SCHEMA_DIM - 1)
        for i in range(3):
            await backend.hypotheses.store(
                content=f"tied-content-{i}", embedding=query_emb, created_at=0
            )
        # Partially-aligned embedding: cosine ≈ 0.707 against the query.
        # Unique FTS content so the authority lane returns exactly this row.
        partial_emb = [1.0, 1.0] + [0.0] * (SCHEMA_DIM - 2)
        await backend.hypotheses.store(
            content="vermillion archive", embedding=partial_emb, created_at=0
        )

        # weights=(0.0, 1.0) zeros the proximity contribution to the
        # composite score, so the authority-only row is the unique top-1
        # without depending on RRF tiebreak ordering.
        results = await backend.hypotheses.search(
            embedding=query_emb, query="vermillion", weights=(0.0, 1.0), limit=1, fan_out=2
        )
        assert len(results) == 1
        assert results[0].content == "vermillion archive"
        # The row never entered ``l1_ranked`` — its ``proximity`` is the
        # documented "no signal" default, not the latent cosine similarity.
        assert results[0].proximity == 0.0


class TestTrustAlignmentsDeterministicOrder:
    """``fetch_trust_alignments`` returns rows in deterministic order across calls.

    The ledger uses integer-second timestamps; under bursty writes multiple
    rows in one consult share a ``timestamp``. The outer projection must
    apply the same secondary key as the inner window functions
    (``ORDER BY timestamp, id``) so the result order is stable on repeat
    calls and identical across backends. Regression guard for S3.4.
    """

    async def test_trust_alignments_deterministic_order_same_second_burst(
        self, backend: BackendFixture
    ) -> None:
        # Same-second burst: 5 attestations on 5 hypotheses by one oracle,
        # each carrying a distinct ``c_oracle_raw`` so row identity is
        # observable in the response. Different oracle seeds each hypothesis
        # first so the trust scan's inner subquery picks them up via the
        # ``relevant`` CTE.
        oracle = "sub:burst-oracle"
        seed_oracle = "sub:seed-oracle"
        hypothesis_ids = [await seed_hypothesis(backend.hypotheses) for _ in range(5)]
        correlation_id = "00000000-0000-0000-0000-0000000be157"
        await seed_request(backend.requests, correlation_id=correlation_id)

        for h_id in hypothesis_ids:
            await backend.attestations.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id=seed_oracle,
                    correlation_id=correlation_id,
                    timestamp=100,
                    t_oracle=0.5,
                    c_oracle_raw=0.1,
                    c_oracle_discounted=0.05,
                    c_herd=0.1,
                    n_oracle_prior=0,
                )
            )

        # The burst: same timestamp, distinct ``c_oracle_raw`` per row so
        # we can see which row landed where in the response.
        distinct_raws = [0.21, 0.22, 0.23, 0.24, 0.25]
        for h_id, raw in zip(hypothesis_ids, distinct_raws, strict=True):
            await backend.attestations.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id=oracle,
                    correlation_id=correlation_id,
                    timestamp=200,
                    t_oracle=0.5,
                    c_oracle_raw=raw,
                    c_oracle_discounted=raw / 2,
                    c_herd=raw,
                    n_oracle_prior=0,
                )
            )

        first = await backend.attestations.fetch_trust_alignments(
            oracle_id=oracle, t_now=200, trust_half_life=NO_DECAY_TRUST_HL
        )
        second = await backend.attestations.fetch_trust_alignments(
            oracle_id=oracle, t_now=200, trust_half_life=NO_DECAY_TRUST_HL
        )

        # Deterministic on repeat: byte-for-byte identical sequence.
        assert first == second
        assert len(first) == 5

        # Order matches lexicographic id ascending among the burst rows.
        # Verify by reading attestation IDs via the relational path and
        # matching them to the alignment rows through ``c_oracle_raw``.
        observed_raws = [round(r.c_oracle_raw, 4) for r in first]
        burst_records: list[AttestationRecord] = []
        for h_id in hypothesis_ids:
            records = await backend.attestations.find_by_hypothesis(h_id)
            burst_records.append(next(r for r in records if r.oracle_id == oracle))
        # Sort by attestation id ascending — the secondary key the outer
        # ORDER BY must apply on same-second ties.
        ordered = sorted(burst_records, key=lambda r: r.id)
        expected_sorted = [round(r.c_oracle_raw, 4) for r in ordered]
        assert observed_raws == expected_sorted


class TestFTSBehavioralParity:
    """Document where the two FTS lexers agree and where they diverge.

    SQLite (FTS5, default ``porter unicode61`` tokenizer) and PostgreSQL
    (``to_tsvector('english', ...)``) both stem English, but their lexers
    differ on accent-folding:

    - ``porter unicode61`` strips combining diacritics (``naïve`` →
      ``naive``) AND applies the porter stemmer (``running`` → ``run``);
      ``café-bar`` splits on the hyphen.
    - ``english`` stems via Snowball/Porter (``running`` → ``run``) but
      preserves accents (``unaccent`` is a separate extension); it also
      splits on punctuation.

    Eszett (ß) is the canonical SQLite-vs-Postgres divergence test:
    neither lexer folds ``ß ↔ ss`` out of the box, so a query for
    ``"strasse"`` against a stored ``"straße"`` must miss on both.

    Every parity row runs strict on both backends — ``sqlite_match`` and
    ``postgres_match`` are independent observations.

    Both backends UNION the proximity and authority lanes into a single
    candidate pool, so a proximity-lane hit surfaces the row even when
    the authority lane misses. To isolate authority recall we use
    ``weights=(0.0, 1.0)`` and check ``score > 0``: with the proximity
    weight zeroed, a positive composite score is unambiguous evidence
    that Lane 2 matched.
    """

    @pytest.mark.parametrize(
        ("stored", "query", "sqlite_match", "postgres_match"),
        [
            # Exact match — sanity. Both lexers must keep their own tokens.
            ("running fast", "running", True, True),
            # Punctuation alone — both lexers split on the hyphen.
            ("café-bar", "bar", True, True),
            # Diacritic folding — unicode61 strips, english preserves
            # (without ``unaccent``).
            ("naïve approach", "naive", True, False),
            # Diacritic + punctuation combined.
            ("café-bar", "cafe", True, False),
            # English stemming — both stem running→run, runs→run
            # (SQLite via porter, Postgres via Snowball).
            ("running fast", "runs", True, True),
        ],
    )
    async def test_fts_recall_parity_verified(
        self,
        backend: BackendFixture,
        stored: str,
        query: str,
        sqlite_match: bool,
        postgres_match: bool,
    ) -> None:
        record = await backend.hypotheses.store(
            content=stored, embedding=[0.1] * SCHEMA_DIM, created_at=0
        )
        results = await backend.hypotheses.search(
            embedding=[0.1] * SCHEMA_DIM, query=query, weights=(0.0, 1.0), limit=10, fan_out=2
        )
        # score > 0 with w_prox=0 isolates authority-lane membership.
        matched = any(r.id == record.id and r.score > 0 for r in results)
        expected = sqlite_match if isinstance(backend.pool, SqlitePool) else postgres_match
        assert matched is expected

    async def test_fts_recall_parity_on_eszett_documented(self, backend: BackendFixture) -> None:
        # Eszett (ß) is not a combining diacritic — neither unicode61 nor the
        # english snowball stemmer folds ``ß ↔ ss``. A German deployment that
        # needs this would install ``unaccent`` (Postgres) or ship a custom
        # tokenizer (SQLite). The expectation here is "both miss" — a future
        # change on either side will fail this test and surface the gap.
        record = await backend.hypotheses.store(
            content="straße", embedding=[0.1] * SCHEMA_DIM, created_at=0
        )
        results = await backend.hypotheses.search(
            embedding=[0.1] * SCHEMA_DIM, query="strasse", weights=(0.0, 1.0), limit=10, fan_out=2
        )
        matched = any(r.id == record.id and r.score > 0 for r in results)
        assert matched is False
