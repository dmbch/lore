"""End-to-end concurrency: two concurrent consults against real Postgres.

The pool-level test in ``test_postgres_pool.py`` proves
``SerializationFailure`` translates to ``RetryableTransactionError``; the
stub tests in ``tests/orchestrator/test_consult_write_concurrency.py``
prove the orchestrator's retry loop works. This test proves the *whole*
chain (orchestrator + retry + real Postgres + SERIALIZABLE) preserves
end-to-end correctness when two consults race on the same hypothesis.
"""

import asyncio
from collections.abc import Sequence

from lore.domain import (
    ArchivistOutput,
    ConsultLoreRequest,
    Resolution,
)
from lore.orchestrator import Orchestrator
from lore.providers import Providers, TaskTypeKey
from lore.repositories import AttestationRecord, RequestRecord
from lore.repositories._postgres.pool import PostgresPool
from lore.repositories._records import generate_id
from tests.orchestrator.conftest import (
    StubCompletion,
    make_interpreter_output,
    make_math,
    make_settings,
)
from tests.repositories.conftest import SCHEMA_DIM, TEST_POSTGRES_CONFIG


class _FixedDimEmbedder:
    """Returns the same 1024-dim vector for every call.

    Matched against the seeded hypothesis (same vector) so the orchestrator's
    proximity-lane search returns that hypothesis as the top candidate.
    """

    def __init__(self, embedding: Sequence[float]) -> None:
        self._embedding = list(embedding)

    async def embed(self, text: str, *, task_type_key: TaskTypeKey | None = None) -> list[float]:
        return list(self._embedding)

    async def embed_many(
        self, texts: list[str], *, task_type_key: TaskTypeKey | None = None
    ) -> list[list[float]]:
        return [await self.embed(t, task_type_key=task_type_key) for t in texts]


class TestConcurrentConsultsAgainstPostgres:
    """Two ``asyncio.gather``'d consults on the same hypothesis both commit.

    Both target the seeded hypothesis (one ``corroborates`` resolution
    each). Under SERIALIZABLE the second committer typically aborts with
    SQLSTATE 40001; the orchestrator's retry loop catches
    ``RetryableTransactionError`` and re-runs against a fresh snapshot.
    The end-state assertion is on ledger integrity: three attestations on
    the hypothesis (1 seed + 2 from the consults), distinct correlation
    IDs, both from the consult oracle. The retry path may or may not be
    exercised on any given run depending on scheduling: that contract is
    covered by the stub and pool-level tests; this test guards correctness
    under realistic concurrency.
    """

    async def test_concurrent_same_oracle_consults_serialize(self, pg_dsn: str) -> None:
        pool = await PostgresPool.create(dsn=pg_dsn, config=TEST_POSTGRES_CONFIG)
        try:
            embedding = [1.0 / SCHEMA_DIM] * SCHEMA_DIM
            seed_correlation_id = "00000000-0000-0000-0000-0000000000e1"

            async with pool.session() as repos:
                hypothesis = await repos.hypotheses.store(
                    content="contested claim", embedding=embedding, created_at=0
                )
                await repos.requests.store(
                    RequestRecord(id=seed_correlation_id, oracle_id="sub:seed", timestamp=0)
                )
                await repos.attestations.append(
                    AttestationRecord(
                        id=generate_id(),
                        hypothesis_id=hypothesis.id,
                        oracle_id="sub:seed",
                        correlation_id=seed_correlation_id,
                        timestamp=1,
                        t_oracle=0.5,
                        c_oracle_raw=0.4,
                        c_oracle_discounted=0.2,
                        c_herd=0.2,
                        n_oracle_prior=0,
                    )
                )

            embedder = _FixedDimEmbedder(embedding)
            interpreter = StubCompletion(make_interpreter_output())
            archivist = StubCompletion(
                ArchivistOutput(
                    reasoning="agreement",
                    answer="agreed",
                    resolutions=[Resolution(corroborates=hypothesis.id)],
                )
            )
            providers = Providers(embedder=embedder, interpreter=interpreter, archivist=archivist)
            orchestrator = Orchestrator(
                pool=pool,
                providers=providers,
                math=make_math(),
                settings=make_settings(),
            )

            request = ConsultLoreRequest(
                question="What does the herd think?",
                hypothesis="contested claim",
                confidence=0.6,
            )

            await asyncio.gather(
                orchestrator.consult(
                    oracle_id="oracle-A", request=request, correlation_id="corr-a"
                ),
                orchestrator.consult(
                    oracle_id="oracle-A", request=request, correlation_id="corr-b"
                ),
            )

            # Both consults committed: 1 seed + 2 from oracle-A.
            async with pool.session() as repos:
                rows = await repos.attestations.find_by_hypothesis(hypothesis.id)

            assert len(rows) == 3
            oracle_a_rows = [r for r in rows if r.oracle_id == "oracle-A"]
            assert len(oracle_a_rows) == 2
            assert {r.correlation_id for r in oracle_a_rows} == {"corr-a", "corr-b"}
        finally:
            await pool.close()
