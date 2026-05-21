"""Real-backend test: orphan request row survives an attestation failure.

The orchestrator writes the request row autocommit at the top of ``consult()``,
**before** entering the providers session or the attestation transaction.
If any downstream step fails — in particular an error raised **inside**
``conn.transaction()`` — the request row must remain as evidence that the
consult was attempted.

This test wraps the real ``AttestationsRepository`` so that ``append()``
raises inside the transaction body. The transaction's ``BEGIN`` has
already run; the wrapper's error triggers ``ROLLBACK``. The
autocommitted request row — written before the transaction opens —
must survive.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast

import aiosqlite
import psycopg
import pytest

from lore.domain import (
    ArchivistOutput,
    ConsultLoreRequest,
    InterpreterOutput,
    Resolution,
    TrustSignal,
)
from lore.orchestrator import Orchestrator
from lore.providers import Providers
from lore.repositories import (
    AttestationRecord,
    AttestationsRepository,
    HypothesisRecord,
    Repositories,
    RepositoryPool,
)
from tests.repositories._orchestrator_fixtures import (
    FixedEmbedder,
    StubCompletion,
    make_math,
    make_settings,
)
from tests.repositories.conftest import BackendFixture


class _FailingAttestationsRepo:
    """Wraps a real AttestationsRepository; ``append()`` raises.

    All reads delegate to the inner repository — ``record()`` calls
    ``fetch_trust_alignments`` and needs real data to proceed to the
    failing ``append()``. The failure occurs **inside**
    ``conn.transaction()``, forcing the transaction to roll back.
    """

    def __init__(self, inner: AttestationsRepository) -> None:
        self._inner = inner

    async def append(
        self,
        *,
        hypothesis_id: str,
        oracle_id: str,
        correlation_id: str,
        timestamp: int,
        t_oracle: float,
        c_oracle_raw: float,
        c_oracle_discounted: float,
        c_herd: float,
        n_oracle_prior: int,
    ) -> None:
        msg = "attestations.append failing inside transaction"
        raise RuntimeError(msg)

    async def find_by_hypothesis(self, hypothesis_id: str) -> list[AttestationRecord]:
        return await self._inner.find_by_hypothesis(hypothesis_id)

    async def find_by_hypotheses(
        self, hypothesis_ids: Sequence[str]
    ) -> dict[str, list[AttestationRecord]]:
        return await self._inner.find_by_hypotheses(hypothesis_ids)

    async def fetch_trust_alignments(
        self,
        *,
        oracle_id: str,
        t_now: int,
        trust_half_life: float,
    ) -> list[TrustSignal]:
        return await self._inner.fetch_trust_alignments(
            oracle_id=oracle_id, t_now=t_now, trust_half_life=trust_half_life
        )


class _RealBackendPool:
    """Wraps the real backend pool with failing attestations injected.

    Each ``session()`` and ``transaction()`` call delegates to the real pool
    so transactions get genuine BEGIN / ROLLBACK semantics — and substitutes
    the failing attestations repo into the bundle.
    """

    def __init__(self, real_pool: RepositoryPool, attestations: AttestationsRepository) -> None:
        self._pool = real_pool
        self._attestations = attestations

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Repositories]:
        async with self._pool.session() as repos:
            yield Repositories(
                hypotheses=repos.hypotheses,
                attestations=self._attestations,
                requests=repos.requests,
            )

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Repositories]:
        async with self._pool.transaction() as repos:
            yield Repositories(
                hypotheses=repos.hypotheses,
                attestations=self._attestations,
                requests=repos.requests,
            )

    async def close(self) -> None:
        pass


async def _read_request_row(
    raw_conn: aiosqlite.Connection | psycopg.AsyncConnection[Any], id: str
) -> tuple[Any, ...] | None:
    """Raw-SQL read bypassing the Protocol layer — test infrastructure only."""
    if isinstance(raw_conn, aiosqlite.Connection):
        cursor = await raw_conn.execute(
            "SELECT id, oracle_id, question, hypothesis FROM requests WHERE id = ?",
            (id,),
        )
        row = await cursor.fetchone()
        return tuple(row) if row is not None else None
    cur = await raw_conn.execute(
        "SELECT id, oracle_id, question, hypothesis FROM requests WHERE id = %s",
        (id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    # Postgres returns ``uuid.UUID`` for the UUID-typed ``id``; SQLite
    # returns the underlying TEXT. Normalise to str so assertions below
    # can compare against the input string.
    return (str(row[0]), *row[1:])


async def _seed_retrievable_hypothesis(backend: BackendFixture) -> HypothesisRecord:
    """Insert a hypothesis so retrieval returns it and classification can resolve it."""
    return await backend.hypotheses.store(
        content="an existing claim", embedding=[0.1] * 1024, created_at=0
    )


async def test_write_path_attestation_failure_inside_transaction_preserves_request_row(
    backend: BackendFixture,
) -> None:
    """When ``attestations.append`` fails inside ``conn.transaction()``, the
    rollback does not touch the autocommitted request row.

    The write path proceeds as follows: the request row is stored
    autocommit at the top of ``consult()``, then ``conn.transaction()``
    opens (BEGIN), then ``record()`` calls ``attestations.append()`` —
    which here raises. The transaction rolls back. The request row,
    written outside the transaction, must still exist.
    """
    correlation_id = "00000000-0000-0000-0000-00000000c0fa"

    # Seed a real hypothesis so classify can resolve an agreement on it —
    # that routes through ``attestations.append()`` directly, without the
    # novel-embedding branch.
    existing = await _seed_retrievable_hypothesis(backend)

    interpreter = StubCompletion(
        InterpreterOutput(
            question="normalized question",
            propositions=["the original proposition"],
            keywords=["kw"],
        )
    )
    archivist = StubCompletion(
        ArchivistOutput(
            reasoning="test reasoning",
            answer="would be recorded",
            resolutions=[
                Resolution(corroborates=existing.id),
            ],
        )
    )
    providers = Providers(
        embedder=cast("Any", FixedEmbedder()),
        interpreter=cast("Any", interpreter),
        archivist=cast("Any", archivist),
    )
    failing_attestations = cast(
        "AttestationsRepository", _FailingAttestationsRepo(backend.attestations)
    )
    pool: RepositoryPool = cast(
        "RepositoryPool", _RealBackendPool(backend.pool, failing_attestations)
    )
    orchestrator = Orchestrator(
        pool=pool,
        providers=providers,
        math=make_math(),
        settings=make_settings(),
    )

    with pytest.raises(RuntimeError, match="attestations.append failing inside transaction"):
        await orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(
                question="What is X?",
                hypothesis="X is a service",
                confidence=0.6,
            ),
            correlation_id=correlation_id,
        )

    # The request row — autocommitted *before* the transaction opened —
    # must survive the rollback.
    row = await _read_request_row(backend.raw_conn, correlation_id)
    assert row is not None
    assert row[0] == correlation_id
    assert row[1] == "oracle-1"
    assert row[2] == "What is X?"
    assert row[3] == "X is a service"
    # And — crucially — no attestation was appended for this correlation_id,
    # proving the transaction actually rolled back.
    attestations = await backend.attestations.find_by_hypothesis(existing.id)
    assert all(a.correlation_id != correlation_id for a in attestations)
