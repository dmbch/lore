"""Retrieval-stage tests: concurrency contract for the session-bound bundle.

The repository pool yields a session-bound ``Repositories`` bundle for the
search stage; ``hypotheses.search`` runs against the connection underlying
that session. psycopg's ``AsyncConnection`` is documented as not safe for
concurrent use across tasks; ``asyncio.gather`` over per-source ``search``
calls violates that contract. SQLite hides the hazard (aiosqlite serializes
through one worker thread); current psycopg versions happen to serialize
via an internal connection lock: the orchestrator must not lean on that.

The test is contract-shaped: with multiple sources, ``search`` calls
must be serial (no two overlap on the wall clock) so that a future
psycopg version dropping its internal lock cannot regress the system.
"""

import asyncio
import importlib.resources
import itertools
import math as _math
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from lore.adapter import LimitsConfig
from lore.config import LoreSettings
from lore.domain import (
    ArchivistOutput,
    ConsultLoreRequest,
    EvidenceInput,
    InterpreterOutput,
    TrustSignal,
)
from lore.math import EpistemicsConfig, MathService
from lore.orchestrator import Orchestrator
from lore.orchestrator.retrieve import embed_novels, embed_sources, enrich, search_candidates
from lore.prompts import PromptsConfig
from lore.providers import EmbeddingModelConfig, ModelConfig, Providers, TaskTypeKey
from lore.repositories import (
    AttestationRecord,
    DecayWindow,
    HypothesisRecord,
    HypothesisResult,
    LedgerView,
    PostgresConfig,
    Repositories,
    RepositoryPool,
    RequestRecord,
    RetrievalConfig,
)
from tests.orchestrator.conftest import StubAttestations, StubCache, make_attestation

# ---------------------------------------------------------------------------
# Stubs: exercise the orchestrator's concurrency contract, not the backend
# ---------------------------------------------------------------------------


_STUB_EMBEDDING: list[float] = [0.01, 0.02, 0.03]


class _StubEmbedder:
    async def embed(self, text: str, *, task_type_key: TaskTypeKey | None = None) -> list[float]:
        del text, task_type_key
        return list(_STUB_EMBEDDING)

    async def embed_many(
        self, texts: list[str], *, task_type_key: TaskTypeKey | None = None
    ) -> list[list[float]]:
        return [await self.embed(t, task_type_key=task_type_key) for t in texts]


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
    concurrent callers (if any) overlap on the wall clock. Tests inspect
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

    async def find_recent(self, *, limit: int) -> list[HypothesisRecord]:
        del limit
        raise NotImplementedError

    async def search(
        self,
        *,
        embedding: Sequence[float],
        keywords: Sequence[str],
        weights: tuple[float, float],
        limit: int,
        fan_out: int,
    ) -> list[HypothesisResult]:
        del embedding, keywords, weights, limit, fan_out
        entry = time.monotonic()
        await asyncio.sleep(0.01)
        exit_ = time.monotonic()
        self.windows.append((entry, exit_))
        return []


class _KeywordRecordingHypotheses:
    """Captures the keyword list handed to ``search``.

    The authority lane needs keyword boundaries intact down to the SQL;
    this stub proves ``search_candidates`` forwards the list, not a
    pre-joined string that has already thrown the boundaries away.
    """

    def __init__(self) -> None:
        self.received: list[Sequence[str]] = []

    async def store(
        self, *, content: str, embedding: Sequence[float], created_at: int
    ) -> HypothesisRecord:
        del content, embedding, created_at
        raise NotImplementedError

    async def find_by_id(self, id: str) -> HypothesisRecord | None:
        del id
        raise NotImplementedError

    async def find_recent(self, *, limit: int) -> list[HypothesisRecord]:
        del limit
        raise NotImplementedError

    async def search(
        self,
        *,
        embedding: Sequence[float],
        keywords: Sequence[str],
        weights: tuple[float, float],
        limit: int,
        fan_out: int,
    ) -> list[HypothesisResult]:
        del embedding, weights, limit, fan_out
        self.received.append(keywords)
        return []


class _NoopAttestations:
    async def append(self, record: AttestationRecord) -> None:
        del record
        raise NotImplementedError

    async def find_by_hypothesis(self, hypothesis_id: str) -> list[AttestationRecord]:
        del hypothesis_id
        return []

    async def find_by_hypotheses(
        self,
        hypothesis_ids: Sequence[str],
        *,
        window: DecayWindow | None = None,
    ) -> dict[str, LedgerView]:
        del window
        return {h: LedgerView(rows=[], oracle_count=0, last_attested=None) for h in hypothesis_ids}

    async def fetch_trust_alignments(
        self,
        *,
        oracle_id: str,
        t_now: int,
        trust_half_life: float,
    ) -> list[TrustSignal]:
        del oracle_id, t_now, trust_half_life
        return []

    async def fetch_herd_evidence(
        self,
        hypothesis_ids: Sequence[str],
        *,
        exclude_oracle: str,
        window: DecayWindow,
    ) -> dict[str, list[EvidenceInput]]:
        del exclude_oracle, window
        return {hid: [] for hid in hypothesis_ids}


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


# Static Protocol verification: catches signature drift at type-check time.
_: type[RepositoryPool] = _StubPool


# ---------------------------------------------------------------------------
# Settings / math helpers
# ---------------------------------------------------------------------------


def _bundled_prompt(name: str) -> Path:
    return Path(str(importlib.resources.files("lore.prompts").joinpath(f"{name}.md")))


def _make_prompts() -> PromptsConfig:
    return PromptsConfig(
        scribe=_bundled_prompt("scribe"),
        interpreter=_bundled_prompt("interpreter"),
        archivist=_bundled_prompt("archivist"),
        contract=_bundled_prompt("contract"),
    )


def _make_settings() -> LoreSettings:
    return LoreSettings(
        dsn="sqlite:///:memory:",
        oidc=None,
        epistemics=EpistemicsConfig(
            attestation_half_life=86400.0, trust_half_life=86400.0, maturity_k=1.0
        ),
        embedding=EmbeddingModelConfig(model="test/embed", dimensions=3),
        fast=ModelConfig(model="test/fast"),
        reasoning=ModelConfig(model="test/reasoning"),
        limits=LimitsConfig(
            question=10000,
            hypothesis=10000,
            context=10000,
            reasoning=10000,
        ),
        retrieval=RetrievalConfig(
            proximity=0.5, authority=0.5, limit=10, fan_out=2, max_keywords=1000
        ),
        postgres=PostgresConfig(min_size=1, max_size=20, timeout=10.0, max_waiting=50),
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
        cache=StubCache(),
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
    scope: they share the connection bound to that session. Concurrent
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
            "search calls overlapped: concurrent work scheduled on the shared connection"
        )

    async def test_retrieve_with_single_source_completes(self) -> None:
        """Sanity control: one source means one search call, no concurrency hazard."""
        hypotheses = _OverlapTrackingHypotheses()
        orchestrator = _make_orchestrator(propositions=[], hypotheses=hypotheses)

        await orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="00000000-0000-0000-0000-0000000000a2",
        )

        assert len(hypotheses.windows) == 1


class TestSearchCandidatesForwardsKeywordList:
    """The authority lane needs keyword boundaries intact down to the SQL.

    ``search_candidates`` must hand ``search`` the truncated keyword list,
    not a space-joined string that has already discarded the boundaries a
    multi-token keyword depends on.
    """

    async def test_search_candidates_passes_keyword_list(self) -> None:
        hypotheses = _KeywordRecordingHypotheses()
        interpreted = InterpreterOutput(
            question="normalized question",
            propositions=["prop A"],
            keywords=["content delivery network", "latency"],
        )

        await search_candidates(
            hypotheses=hypotheses,
            interpreted=interpreted,
            source_embeddings=[list(_STUB_EMBEDDING)],
            settings=_make_settings(),
        )

        assert hypotheses.received == [["content delivery network", "latency"]]


class _BatchRecordingEmbedder:
    """Records ``embed_many`` batches; hands out one distinct vector per text."""

    def __init__(self) -> None:
        self.batches: list[tuple[list[str], TaskTypeKey | None]] = []
        self._vectors: dict[str, list[float]] = {}

    def vector(self, text: str) -> list[float]:
        return self._vectors.setdefault(text, [float(len(self._vectors))])

    async def embed_many(
        self, texts: list[str], *, task_type_key: TaskTypeKey | None = None
    ) -> list[list[float]]:
        self.batches.append((list(texts), task_type_key))
        return [self.vector(t) for t in texts]


def _batch_session(embedder: _BatchRecordingEmbedder) -> Providers:
    completion = _StubCompletion(ArchivistOutput(reasoning="r", answer="a"))
    return Providers(embedder=embedder, interpreter=completion, archivist=completion)


class TestEmbedBatchesPerTaskType:
    """One embedding request per task-type group, not one per source.

    The latency win was already banked by ``gather``; the batch buys request
    count, which is RPM headroom under parallel e2e workers.
    """

    async def test_embed_sources_issues_one_call_per_task_type(self) -> None:
        embedder = _BatchRecordingEmbedder()
        interpreted = InterpreterOutput(
            question="normalized question",
            propositions=["prop A", "prop B", "prop C"],
            keywords=["kw1"],
        )

        await embed_sources(
            providers=_batch_session(embedder),
            interpreted=interpreted,
            question="What is X?",
        )

        assert embedder.batches == [
            (["What is X?"], "question"),
            (["prop A", "prop B", "prop C"], "verification"),
        ]

    async def test_embed_novels_issues_one_batch(self) -> None:
        embedder = _BatchRecordingEmbedder()

        await embed_novels(
            providers=_batch_session(embedder),
            novels=["novel A", "novel B", "novel C"],
        )

        assert embedder.batches == [(["novel A", "novel B", "novel C"], "document")]

    async def test_source_embedding_order_matches_source_order(self) -> None:
        embedder = _BatchRecordingEmbedder()
        interpreted = InterpreterOutput(
            question="normalized question",
            propositions=["prop A", "prop B"],
            keywords=["kw1"],
        )

        result = await embed_sources(
            providers=_batch_session(embedder),
            interpreted=interpreted,
            question="What is X?",
        )

        assert result == [
            embedder.vector("What is X?"),
            embedder.vector("prop A"),
            embedder.vector("prop B"),
        ]


class TestEnrichClampsEngineFloatNoise:
    """Engine cosine similarity can overshoot the algebraic range by an ulp.

    pgvector and sqlite-vec compute ``1 - distance`` in engine floats;
    near-identical vectors can land at ``1 + ulp``. ``SearchResult`` bounds
    ``proximity`` as ``SignedUnitInterval``, so an unclamped pass-through
    turns a healthy consult into a validation error.
    """

    def _candidate(self, *, proximity: float) -> HypothesisResult:
        return HypothesisResult(
            id="00000000-0000-0000-0000-0000000000b1",
            content="content",
            created_at=0,
            score=0.5,
            proximity=proximity,
        )

    async def test_enrich_clamps_proximity_overshoot_to_the_rails(self) -> None:
        for overshoot, rail in [
            (_math.nextafter(1.0, 2.0), 1.0),
            (_math.nextafter(-1.0, -2.0), -1.0),
        ]:
            enriched = await enrich(
                candidates=[self._candidate(proximity=overshoot)],
                attestations=_NoopAttestations(),
                math=_make_math(),
                settings=_make_settings(),
                t_now=0,
            )
            assert enriched[0].proximity == rail


class TestEnrichDecayWindow:
    """Reads stop fetching rows whose decayed weight is noise."""

    async def test_enrich_ignores_rows_beyond_decay_window(self) -> None:
        """Rows older than 5 half-lives leave the fusion; the summary sees them.

        The stale row (10 half-lives old, c 0.9) would still contribute
        ~9e-4 after decay, so its absence pins the fetch cutoff, not the
        decay algebra. The fresh row alone gives c_herd = 0.3 exactly.
        oracle_count and last_attested stay full-history: the oracle
        whose only row aged out still counts, so this hypothesis is
        stale, not unattested.
        """
        t_now = 2_000_000_000
        half_life = 86_400  # matches _make_settings' attestation_half_life
        candidate = HypothesisResult(
            id="00000000-0000-0000-0000-0000000000b1",
            content="content",
            created_at=0,
            score=0.5,
            proximity=0.5,
        )
        stub = StubAttestations(
            by_hypotheses={
                candidate.id: [
                    make_attestation(
                        hypothesis_id=candidate.id,
                        c_oracle_discounted=0.9,
                        timestamp=t_now - 10 * half_life,
                    ),
                    make_attestation(
                        hypothesis_id=candidate.id,
                        c_oracle_discounted=0.3,
                        timestamp=t_now,
                        oracle_id="oracle-2",
                    ),
                ]
            }
        )

        enriched = await enrich(
            candidates=[candidate],
            attestations=stub,
            math=_make_math(),
            settings=_make_settings(),
            t_now=t_now,
        )

        assert abs(enriched[0].c_herd - 0.3) < 1e-9
        assert enriched[0].oracle_count == 2
        assert enriched[0].last_attested == date(2033, 5, 18)
