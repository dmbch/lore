"""Orchestrator retry loop on ``RetryableTransactionError``.

The write-path transaction runs at PG SERIALIZABLE (see ``PostgresPool``).
When two writers collide on the same hypothesis, one aborts with
SQLSTATE 40001, surfaced as ``RetryableTransactionError``. The
orchestrator retries ``record()`` up to ``RECORD_MAX_ATTEMPTS`` times with
equal-jitter exponential backoff before propagating.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from lore.domain import (
    ArchivistOutput,
    Resolution,
    RetryableTransactionError,
)
from lore.orchestrator import Orchestrator
from lore.orchestrator.orchestrator import RECORD_MAX_ATTEMPTS
from lore.providers import Providers
from lore.repositories import Repositories
from tests.orchestrator.conftest import (
    StubAttestations,
    StubCompletion,
    StubEmbedder,
    StubHypotheses,
    StubRequests,
    make_interpreter_output,
    make_math,
    make_settings,
    write_request,
)

_BASE_SECONDS = 0.02


def _backoff_range(attempt: int) -> tuple[float, float]:
    """Equal-jitter window for ``attempt``: [ceiling/2, ceiling]."""
    ceiling = _BASE_SECONDS * 2**attempt
    return ceiling / 2, ceiling


class _RetryingStubPool:
    """Pool stub whose ``transaction()`` raises ``RetryableTransactionError``
    on the first ``raise_count`` enters, then yields the bundled repos.
    """

    def __init__(self, repos: Repositories, *, raise_count: int) -> None:
        self._repos = repos
        self._raise_count = raise_count
        self.transaction_calls = 0

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Repositories]:
        yield self._repos

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Repositories]:
        self.transaction_calls += 1
        if self.transaction_calls <= self._raise_count:
            raise RetryableTransactionError("simulated serialization failure")
        yield self._repos

    async def close(self) -> None:
        pass


def _make_orchestrator_with_pool(pool: _RetryingStubPool) -> Orchestrator:
    embedder = StubEmbedder()
    interpreter = StubCompletion(make_interpreter_output())
    archivist = StubCompletion(
        ArchivistOutput(
            reasoning="test",
            answer="answer",
            resolutions=[Resolution(contributes="atomic A")],
        )
    )
    providers = Providers(embedder=embedder, interpreter=interpreter, archivist=archivist)
    return Orchestrator(
        pool=pool,
        providers=providers,
        math=make_math(),
        settings=make_settings(),
    )


class TestOrchestratorRecordRetry:
    async def test_consult_succeeds_after_one_retryable_failure(self) -> None:
        request_store = StubRequests()
        hypotheses = StubHypotheses()
        attestations = StubAttestations()
        repos = Repositories(
            hypotheses=hypotheses,
            attestations=attestations,
            requests=request_store,
        )
        raise_count = 1
        pool = _RetryingStubPool(repos, raise_count=raise_count)
        orchestrator = _make_orchestrator_with_pool(pool)

        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        with patch("lore.orchestrator.orchestrator.asyncio.sleep", fake_sleep):
            await orchestrator.consult(
                oracle_id="oracle-1", request=write_request(), correlation_id="corr-retry"
            )

        # The loop iterated once more than it raised — last attempt succeeded.
        assert pool.transaction_calls == raise_count + 1
        # One backoff after attempt 0 — within the equal-jitter window for it.
        assert len(sleeps) == 1
        lo, hi = _backoff_range(0)
        assert lo <= sleeps[0] <= hi
        # The retry committed: a single attestation was appended.
        assert len(attestations.appended) == 1

    async def test_consult_succeeds_after_two_retryable_failures(self) -> None:
        request_store = StubRequests()
        hypotheses = StubHypotheses()
        attestations = StubAttestations()
        repos = Repositories(
            hypotheses=hypotheses,
            attestations=attestations,
            requests=request_store,
        )
        raise_count = 2
        pool = _RetryingStubPool(repos, raise_count=raise_count)
        orchestrator = _make_orchestrator_with_pool(pool)

        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        with patch("lore.orchestrator.orchestrator.asyncio.sleep", fake_sleep):
            await orchestrator.consult(
                oracle_id="oracle-1", request=write_request(), correlation_id="corr-retry2"
            )

        assert pool.transaction_calls == raise_count + 1
        # Two backoffs, each inside its equal-jitter window for the attempt.
        assert len(sleeps) == 2
        for attempt, sleep in enumerate(sleeps):
            lo, hi = _backoff_range(attempt)
            assert lo <= sleep <= hi
        assert len(attestations.appended) == 1

    async def test_consult_propagates_after_max_attempts_exhausted(self) -> None:
        request_store = StubRequests()
        hypotheses = StubHypotheses()
        attestations = StubAttestations()
        repos = Repositories(
            hypotheses=hypotheses,
            attestations=attestations,
            requests=request_store,
        )
        pool = _RetryingStubPool(repos, raise_count=RECORD_MAX_ATTEMPTS + 5)
        orchestrator = _make_orchestrator_with_pool(pool)

        async def fake_sleep(_delay: float) -> None:
            pass

        with (
            patch("lore.orchestrator.orchestrator.asyncio.sleep", fake_sleep),
            pytest.raises(RetryableTransactionError),
        ):
            await orchestrator.consult(
                oracle_id="oracle-1", request=write_request(), correlation_id="corr-exhaust"
            )

        assert pool.transaction_calls == RECORD_MAX_ATTEMPTS
        assert len(attestations.appended) == 0
