"""Retrieval-stage tests — concurrency contract for the session-bound bundle.

The repository pool yields a session-bound ``Repositories`` bundle for the
search stage; ``hypotheses.search`` runs against the connection underlying
that session. psycopg's ``AsyncConnection`` is documented as not safe for
concurrent use across tasks; ``asyncio.gather`` over per-source ``search``
calls violates that contract. SQLite hides the hazard (aiosqlite serializes
through one worker thread); current psycopg versions happen to serialize
via an internal connection lock — the orchestrator must not lean on that.

The test is contract-shaped: with multiple sources, ``search`` calls
must be serial — no two overlap on the wall clock — so that a future
psycopg version dropping its internal lock cannot regress the system.
"""

import asyncio
import importlib.resources
import itertools
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from lore.adapter import LimitsConfig
from lore.config import LoreSettings
from lore.config.types import (
    DecayConfig,
    TrustConfig,
)
from lore.domain import (
    ArchivistOutput,
    ConsultLoreRequest,
    InterpreterOutput,
    TrustSignal,
)
from lore.math import MathService
from lore.orchestrator import Orchestrator
from lore.prompts import PromptsConfig
from lore.providers import EmbeddingModelConfig, ModelConfig, Providers, TaskTypeKey
from lore.repositories import (
    AttestationRecord,
    HypothesisRecord,
    HypothesisResult,
    PostgresConfig,
    Repositories,
    RepositoryPool,
    RequestRecord,
    RetrievalConfig,
)

# ---------------------------------------------------------------------------
# Stubs — exercise the orchestrator's concurrency contract, not the backend
# ---------------------------------------------------------------------------


_STUB_EMBEDDING: list[float] = [0.01, 0.02, 0.03]


class _StubEmbedder:
    async def embed(self, text: str, *, task_type_key: TaskTypeKey | None = None) -> list[float]:
        del text, task_type_key
        return list(_STUB_EMBEDDING)


class _StubCompletion:
    """Returns a fixed Pydantic model regardless of the requested response_model."""

    def __init__(self, output: BaseModel) -> None:
        self._output = output

    async def complete[T: BaseModel](self, *, response_model: type[T], system: str, user: str) -> T:
        del response_model, system, user
        # The stub is constructed with an output instance whose runtime type matches the
        # response_model used at the call site. The cast expresses that structural intent;
        # the type system can't see the construction-time correspondence.
        return cast(T, self._output)


class _OverlapTrackingHypotheses:
    """Records entry/exit timestamps per ``search`` call.

    A short ``asyncio.sleep`` inside ``search`` opens a window in which
    concurrent callers — if any — overlap on the wall clock. Tests inspect
    the recorded windows to detect contract violations.
    """

    def __init__(self) -> None:
        self.windows: list[tuple[float, float]] = []

    async def store(
        self, *, content: str, embedding: Sequence[float], created_at: int
    ) -> HypothesisRecord:
        del content, embedding, created_at
        raise NotImplementedError

    async def find_by_id(self, id: str) -> HypothesisRecord | None:
        del id
        raise NotImplementedError

    async def search(
        self,
        *,
        embedding: Sequence[float],
        query: str,
        weights: tuple[float, float],
        limit: int,
        fan_out: int,
    ) -> list[HypothesisResult]:
        del embedding, query, weights, limit, fan_out
        entry = time.monotonic()
        await asyncio.sleep(0.01)
        exit_ = time.monotonic()
        self.windows.append((entry, exit_))
        return []


class _NoopAttestations:
    async def append(self, record: AttestationRecord) -> None:
        del record
        raise NotImplementedError

    async def find_by_hypothesis(self, hypothesis_id: str) -> list[AttestationRecord]:
        del hypothesis_id
        return []

    async def find_by_hypotheses(
        self, hypothesis_ids: Sequence[str]
    ) -> dict[str, list[AttestationRecord]]:
        del hypothesis_ids
        return {}

    async def fetch_trust_alignments(
        self,
        *,
        oracle_id: str,
        t_now: int,
        trust_half_life: float,
    ) -> list[TrustSignal]:
        del oracle_id, t_now, trust_half_life
        return []


class _NoopRequests:
    async def store(self, record: RequestRecord) -> None:
        del record
        return None


class _StubPool:
    def __init__(self, repos: Repositories) -> None:
        self._repos = repos

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Repositories]:
        yield self._repos

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Repositories]:
        yield self._repos

    async def close(self) -> None:
        return None


# Static Protocol verification — catches signature drift at type-check time.
_: type[RepositoryPool] = _StubPool


# ---------------------------------------------------------------------------
# Settings / math helpers
# ---------------------------------------------------------------------------


def _bundled_prompt(name: str) -> Path:
    return Path(str(importlib.resources.files("lore.prompts").joinpath(f"{name}.md")))


def _make_prompts() -> PromptsConfig:
    return PromptsConfig(
        scribe=_bundled_prompt("scribe"),
        consult=_bundled_prompt("consult"),
        interpreter=_bundled_prompt("interpreter"),
        archivist=_bundled_prompt("archivist"),
    )


def _make_settings() -> LoreSettings:
    return LoreSettings(
        dsn="sqlite:///:memory:",
        oidc=None,
        decay=DecayConfig(attestation=86400.0, trust=86400.0),
        embedding=EmbeddingModelConfig(model="test/embed", dimensions=3),
        fast=ModelConfig(model="test/fast"),
        reasoning=ModelConfig(model="test/reasoning"),
        trust=TrustConfig(maturity=1.0),
        limits=LimitsConfig(
            question=10000,
            hypothesis=10000,
            context=10000,
            reasoning=10000,
        ),
        retrieval=RetrievalConfig(
            proximity=0.5, authority=0.5, limit=10, fan_out=2, max_keywords=1000
        ),
        postgres=PostgresConfig(min_size=1, max_size=20, getconn_timeout=10.0, max_waiting=50),
        prompts=_make_prompts(),
    )


def _make_math() -> MathService:
    return MathService(c_half_life=86400.0, maturity_k=1.0, t_half_life=86400.0)


def _make_orchestrator(
    propositions: list[str], hypotheses: _OverlapTrackingHypotheses
) -> Orchestrator:
    repos = Repositories(
        hypotheses=hypotheses,
        attestations=_NoopAttestations(),
        requests=_NoopRequests(),
    )
    pool = _StubPool(repos)
    providers = Providers(
        embedder=_StubEmbedder(),
        interpreter=_StubCompletion(
            InterpreterOutput(
                question="normalized question",
                propositions=propositions,
                keywords=["kw1"],
            )
        ),
        archivist=_StubCompletion(ArchivistOutput(reasoning="r", answer="a")),
    )
    return Orchestrator(
        pool=pool,
        providers=providers,
        math=_make_math(),
        settings=_make_settings(),
    )


def _windows_overlap(windows: Sequence[tuple[float, float]]) -> bool:
    """True iff any pair of windows overlaps in time."""
    sorted_windows = sorted(windows)
    for (_, prev_exit), (next_entry, _) in itertools.pairwise(sorted_windows):
        if next_entry < prev_exit:
            return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRetrieveDoesNotShareConnectionConcurrently:
    """The search stage must serialize ``hypotheses.search`` calls.

    All per-source ``search`` calls run inside the same ``pool.session()``
    scope — they share the connection bound to that session. Concurrent
    ``search`` invocations on that connection are unsafe by psycopg's
    contract, even when current psycopg versions happen to serialize via
    an internal lock. The orchestrator owns the contract; do not lean on
    the backend for it.
    """

    async def test_retrieve_with_multiple_sources_does_not_share_connection_concurrently(
        self,
    ) -> None:
        hypotheses = _OverlapTrackingHypotheses()
        orchestrator = _make_orchestrator(
            propositions=["proposition A", "proposition B"], hypotheses=hypotheses
        )

        await orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="00000000-0000-0000-0000-0000000000a1",
        )

        # question + 2 propositions = 3 sources => 3 search calls.
        assert len(hypotheses.windows) == 3
        assert not _windows_overlap(hypotheses.windows), (
            "search calls overlapped — concurrent work scheduled on the shared connection"
        )

    async def test_retrieve_with_single_source_completes(self) -> None:
        """Sanity control: one source means one search call — no concurrency hazard."""
        hypotheses = _OverlapTrackingHypotheses()
        orchestrator = _make_orchestrator(propositions=[], hypotheses=hypotheses)

        await orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="00000000-0000-0000-0000-0000000000a2",
        )

        assert len(hypotheses.windows) == 1
