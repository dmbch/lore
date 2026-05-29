"""Real-backend test: mid-batch ``append`` failure rolls back atomically.

A single ``consult`` call may produce multiple writes; IDEA.md and
``docs/architecture.md`` pin them to one transaction. If any step fails,
the transaction rolls back: the first successful write to
``attestations`` reverts, and any novel hypothesis stored inside the
transaction body disappears with it.

This test wraps the real ``AttestationsRepository`` so the second
``append()`` raises ``StorageError`` after the first has already
succeeded. The orchestrator-level exception unwinds; the transaction
rolls back; the seeded prior attestation is the only row that survives
on the existing hypothesis, and no row exists for the novel.
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
    StorageError,
    TrustSignal,
)
from lore.orchestrator import Orchestrator
from lore.providers import Providers
from lore.repositories import (
    AttestationRecord,
    AttestationsRepository,
    Repositories,
    RepositoryPool,
)
from lore.repositories.records import generate_id
from tests.repositories._orchestrator_fixtures import (
    FixedEmbedder,
    StubCompletion,
    make_math,
    make_settings,
)
from tests.repositories.conftest import (
    BackendFixture,
    seed_request,
)


class _FailingOnSecondCallAttestationsRepo:
    """Delegates ``append`` on the first call, raises ``StorageError`` on the second.

    Reads and the first ``append`` delegate to the transaction-bound inner
    repository so the surviving write is the one the rollback must revert.
    Exposes ``calls`` so the test can assert the failure actually fired —
    otherwise a future change that drops below two ``append`` calls would
    make the rollback assertion vacuous.
    """

    def __init__(self, inner: AttestationsRepository) -> None:
        self._inner = inner
        self.calls = 0

    async def append(self, record: AttestationRecord) -> None:
        self.calls += 1
        if self.calls == 1:
            await self._inner.append(record)
            return
        msg = "attestations.append failing on second call"
        raise StorageError(msg)

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
    """Wraps the real pool, substituting the transaction-scoped attestations repo.

    The failing wrapper must delegate to the connection bound to the active
    ``pool.transaction()`` scope — otherwise the first ``append`` lands on
    a different connection and the rollback under test has nothing to undo.
    ``last_wrapper`` exposes the most recently constructed wrapper so the
    test can assert how many ``append`` calls actually fired.
    """

    def __init__(self, real_pool: RepositoryPool) -> None:
        self._pool = real_pool
        self.last_wrapper: _FailingOnSecondCallAttestationsRepo | None = None

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Repositories]:
        async with self._pool.session() as repos:
            yield repos

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Repositories]:
        async with self._pool.transaction() as repos:
            wrapper = _FailingOnSecondCallAttestationsRepo(repos.attestations)
            self.last_wrapper = wrapper
            yield Repositories(
                hypotheses=repos.hypotheses,
                attestations=cast("AttestationsRepository", wrapper),
                requests=repos.requests,
            )

    async def close(self) -> None:
        pass


async def _count_hypotheses_with_content(
    raw_conn: aiosqlite.Connection | psycopg.AsyncConnection[Any], content: str
) -> int:
    """Raw-SQL read bypassing the Protocol layer — test infrastructure only."""
    if isinstance(raw_conn, aiosqlite.Connection):
        cursor = await raw_conn.execute(
            "SELECT COUNT(*) FROM hypotheses WHERE content = ?", (content,)
        )
        row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0
    cur = await raw_conn.execute("SELECT COUNT(*) FROM hypotheses WHERE content = %s", (content,))
    row = await cur.fetchone()
    return int(row[0]) if row is not None else 0


async def test_recorder_failure_rolls_back_attestations(
    backend: BackendFixture,
) -> None:
    """A mid-batch ``append`` failure rolls back every write the transaction made.

    Two resolutions: one corroborate on a seeded hypothesis, one contribute
    on a novel (``hypotheses.store`` + a second ``append`` — which raises).
    After rollback, the seeded hypothesis carries only its prior
    attestation, and the novel never existed.
    """
    correlation_id = "00000000-0000-0000-0000-000000000abc"
    prior_correlation_id = "00000000-0000-0000-0000-000000000def"
    novel_content = "a brand new claim"

    # Seed an existing hypothesis plus one prior attestation. The prior is
    # what the Recorder's attestation refetch returns; the assertion below
    # verifies it survives unchanged through the rollback.
    existing = await backend.hypotheses.store(
        content="an existing claim", embedding=[0.1] * 1024, created_at=0
    )
    await seed_request(backend.requests, correlation_id=prior_correlation_id)
    await backend.attestations.append(
        AttestationRecord(
            id=generate_id(),
            hypothesis_id=existing.id,
            oracle_id="sub:oracle-1",
            correlation_id=prior_correlation_id,
            timestamp=1000,
            t_oracle=0.5,
            c_oracle_raw=0.5,
            c_oracle_discounted=0.25,
            c_herd=0.4,
            n_oracle_prior=0,
        )
    )

    interpreter = StubCompletion(
        InterpreterOutput(
            question="normalized question",
            propositions=["an existing claim", novel_content],
            keywords=["kw"],
        )
    )
    archivist = StubCompletion(
        ArchivistOutput(
            reasoning="test reasoning",
            answer="would be recorded",
            resolutions=[
                Resolution(corroborates=existing.id),
                Resolution(contributes=novel_content),
            ],
        )
    )
    providers = Providers(
        embedder=cast("Any", FixedEmbedder()),
        interpreter=cast("Any", interpreter),
        archivist=cast("Any", archivist),
    )
    failing_pool = _RealBackendPool(backend.pool)
    pool: RepositoryPool = cast("RepositoryPool", failing_pool)
    orchestrator = Orchestrator(
        pool=pool,
        providers=providers,
        math=make_math(),
        settings=make_settings(),
    )

    with pytest.raises(StorageError, match="attestations.append failing on second call"):
        await orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(
                question="What is X?",
                hypothesis="X is a service",
                confidence=0.6,
            ),
            correlation_id=correlation_id,
        )

    # Pin that the failure actually fired mid-batch: the first ``append``
    # succeeded inside the transaction, the second raised. Without this,
    # a refactor that drops below two ``append`` calls would make the
    # rollback assertions below pass vacuously.
    assert failing_pool.last_wrapper is not None
    assert failing_pool.last_wrapper.calls == 2

    # The first ``append`` succeeded inside the transaction body, then
    # rolled back. Only the prior attestation, written outside the
    # transaction, must remain.
    attestations = await backend.attestations.find_by_hypothesis(existing.id)
    assert len(attestations) == 1
    assert attestations[0].correlation_id == prior_correlation_id

    # The novel hypothesis was stored inside the same transaction; rollback
    # erases it.
    assert await _count_hypotheses_with_content(backend.raw_conn, novel_content) == 0
