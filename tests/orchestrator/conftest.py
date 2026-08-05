"""Shared stubs, helpers, and fixtures for orchestrator tests.

Uses Protocol-satisfying stubs for providers and repositories: the
orchestrator tests verify wiring, not backend behavior. The sole
pyright suppression is reportReturnType on StubCompletion.complete
(generic Protocol methods cannot be satisfied by fixed-type stubs).
"""

import importlib.resources
from collections.abc import AsyncGenerator, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import structlog
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel
from structlog.typing import EventDict

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
from lore.prompts import PromptsConfig
from lore.providers import EmbeddingModelConfig, ModelConfig, Providers, TaskTypeKey
from lore.repositories import (
    AttestationRecord,
    CacheEntry,
    DecayWindow,
    HypothesisRecord,
    HypothesisResult,
    LedgerView,
    PostgresConfig,
    Repositories,
    RequestRecord,
    RetrievalConfig,
)

# ---------------------------------------------------------------------------
# Stub providers: external services, mocking is appropriate
# ---------------------------------------------------------------------------

STUB_EMBEDDING = [0.1, 0.2, 0.3]


class StubEmbedder:
    """Returns a fixed vector. Tracks calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, TaskTypeKey | None]] = []

    async def embed(self, text: str, *, task_type_key: TaskTypeKey | None = None) -> list[float]:
        self.calls.append((text, task_type_key))
        return STUB_EMBEDDING

    async def embed_many(
        self, texts: list[str], *, task_type_key: TaskTypeKey | None = None
    ) -> list[list[float]]:
        return [await self.embed(t, task_type_key=task_type_key) for t in texts]


class StubCompletion:
    """Fixed-response completion stub. Satisfies Completer Protocol shape.

    Returns the pre-set output regardless of response_model. The orchestrator
    always passes the correct model type: the stub trusts this for testing.
    """

    def __init__(self, output: BaseModel) -> None:
        self._output = output
        self.calls: list[tuple[str, str]] = []

    async def complete[T: BaseModel](self, *, response_model: type[T], system: str, user: str) -> T:
        self.calls.append((system, user))
        return self._output  # pyright: ignore[reportReturnType]


# ---------------------------------------------------------------------------
# Stub repositories: in-memory, Protocol-satisfying
# ---------------------------------------------------------------------------


class StubHypotheses:
    """In-memory hypothesis store with search and find_recent stubs.

    ``recent`` seeds ``find_recent``; unseeded (None) it stays a loud
    NotImplementedError guard for tests that must never reach it.
    """

    def __init__(
        self,
        search_results: list[HypothesisResult] | None = None,
        recent: list[HypothesisRecord] | None = None,
    ) -> None:
        self._results = search_results or []
        self._recent = recent
        self.stored: list[tuple[str, Sequence[float], int]] = []
        self._next_id = 0

    async def store(
        self, *, content: str, embedding: Sequence[float], created_at: int
    ) -> HypothesisRecord:
        self.stored.append((content, embedding, created_at))
        # Deterministic but valid UUID: first segment is exactly 8 hex chars.
        record_id = f"aaa{self._next_id:05d}-e29b-41d4-a716-446655440000"
        self._next_id += 1
        return HypothesisRecord.model_construct(
            id=record_id, content=content, created_at=created_at
        )

    async def find_by_id(self, id: str) -> HypothesisRecord | None:
        raise NotImplementedError

    async def find_recent(self, *, limit: int) -> list[HypothesisRecord]:
        if self._recent is None:
            raise NotImplementedError
        return self._recent[:limit]

    async def search(
        self,
        *,
        embedding: Sequence[float],
        keywords: Sequence[str],
        weights: tuple[float, float],
        limit: int,
        fan_out: int,
    ) -> list[HypothesisResult]:
        return self._results


class StubAttestations:
    """In-memory attestation ledger."""

    def __init__(
        self,
        by_hypotheses: dict[str, list[AttestationRecord]] | None = None,
        trust_alignments: list[TrustSignal] | None = None,
        herd_evidence: dict[str, list[EvidenceInput]] | None = None,
    ) -> None:
        self._by_hypotheses = by_hypotheses or {}
        self._trust_alignments = trust_alignments or []
        self._herd_evidence = herd_evidence or {}
        self.appended: list[AttestationRecord] = []

    async def append(self, record: AttestationRecord) -> None:
        self.appended.append(record)

    async def find_by_hypothesis(self, hypothesis_id: str) -> list[AttestationRecord]:
        return self._by_hypotheses.get(hypothesis_id, [])

    async def find_by_hypotheses(
        self,
        hypothesis_ids: Sequence[str],
        *,
        window: DecayWindow | None = None,
    ) -> dict[str, LedgerView]:
        def view(records: list[AttestationRecord]) -> LedgerView:
            rows = records
            if window is not None:
                rows = [r for r in records if window.start <= r.timestamp <= window.t_now]
            return LedgerView(
                rows=rows,
                oracle_count=len(records),
                last_attested=max((r.timestamp for r in records), default=None),
            )

        return {hid: view(self._by_hypotheses.get(hid, [])) for hid in hypothesis_ids}

    async def fetch_trust_alignments(
        self,
        *,
        oracle_id: str,
        t_now: int,
        trust_half_life: float,
    ) -> list[TrustSignal]:
        return self._trust_alignments

    async def fetch_herd_evidence(
        self,
        hypothesis_ids: Sequence[str],
        *,
        exclude_oracle: str,
        window: DecayWindow,
    ) -> dict[str, list[EvidenceInput]]:
        return {hid: self._herd_evidence.get(hid, []) for hid in hypothesis_ids}


class StubRequests:
    """In-memory request store. Tracks stored records."""

    def __init__(self) -> None:
        self.stored: list[RequestRecord] = []

    async def store(self, record: RequestRecord) -> None:
        self.stored.append(record)


class StubCache:
    """Loud guard: the orchestrator never touches the OAuth token cache."""

    async def get_entry(self, *, collection: str, key: str) -> CacheEntry | None:
        raise NotImplementedError

    async def put_entry(
        self,
        *,
        collection: str,
        key: str,
        value: str,
        created_at: int,
        expires_at: int | None,
    ) -> None:
        raise NotImplementedError

    async def delete_entry(self, *, collection: str, key: str) -> bool:
        raise NotImplementedError

    async def delete_expired(self, *, now: int) -> int:
        raise NotImplementedError


class StubPool:
    """Stub pool yielding the real Repositories NamedTuple from session/transaction."""

    def __init__(self, repos: Repositories) -> None:
        self._repos = repos

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Repositories]:
        yield self._repos

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Repositories]:
        yield self._repos

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class Fixture:
    """All stubs needed for read and write path assertions."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        embedder: StubEmbedder,
        interpreter: StubCompletion,
        archivist: StubCompletion,
        requests: StubRequests,
        hypotheses: StubHypotheses,
        attestations: StubAttestations,
        pool: StubPool,
    ) -> None:
        self.orchestrator = orchestrator
        self.embedder = embedder
        self.interpreter = interpreter
        self.archivist = archivist
        self.requests = requests
        self.hypotheses = hypotheses
        self.attestations = attestations
        self.pool = pool


def _bundled_prompt(name: str) -> Path:
    return Path(str(importlib.resources.files("lore.prompts").joinpath(f"{name}.md")))


def _make_prompts() -> PromptsConfig:
    return PromptsConfig(
        scribe=_bundled_prompt("scribe"),
        interpreter=_bundled_prompt("interpreter"),
        archivist=_bundled_prompt("archivist"),
        contract=_bundled_prompt("contract"),
    )


def make_settings() -> LoreSettings:
    """Build a minimal LoreSettings for the orchestrator."""
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


def make_math() -> MathService:
    return MathService(
        c_half_life=86400.0,
        maturity_k=1.0,
        t_half_life=86400.0,
    )


def make_interpreter_output(
    question: str = "normalized question",
    keywords: list[str] | None = None,
    propositions: list[str] | None = None,
) -> InterpreterOutput:
    # Default propositions sized generously so unrelated tests don't trip
    # the one-resolution-per-proposition overflow guard. Tests that exercise
    # the bound itself live in test_validate_resolutions.py and pass
    # propositions explicitly.
    return InterpreterOutput(
        question=question,
        propositions=propositions if propositions is not None else [f"prop {i}" for i in range(16)],
        keywords=keywords or ["keyword1", "keyword2"],
    )


def make_hypothesis_result(
    id: str = "550e8400-e29b-41d4-a716-446655440000",
    content: str = "test hypothesis",
    created_at: int = 1000000,
    score: float = 0.4,
    proximity: float = 0.95,
) -> HypothesisResult:
    return HypothesisResult.model_construct(
        id=id,
        content=content,
        created_at=created_at,
        score=score,
        proximity=proximity,
    )


def make_attestation(
    hypothesis_id: str = "550e8400-e29b-41d4-a716-446655440000",
    c_oracle_discounted: float = 0.3,
    timestamp: int = 2000000000,
    oracle_id: str = "oracle-1",
    c_herd: float = 0.3,
    n_oracle_prior: int = 0,
) -> AttestationRecord:
    return AttestationRecord.model_construct(
        id="660e8400-e29b-41d4-a716-446655440000",
        hypothesis_id=hypothesis_id,
        oracle_id=oracle_id,
        correlation_id="corr-1",
        timestamp=timestamp,
        t_oracle=0.5,
        c_oracle_raw=0.6,
        c_oracle_discounted=c_oracle_discounted,
        c_herd=c_herd,
        n_oracle_prior=n_oracle_prior,
    )


def make_orchestrator(
    *,
    search_results: list[HypothesisResult] | None = None,
    by_hypotheses: dict[str, list[AttestationRecord]] | None = None,
    trust_alignments: list[TrustSignal] | None = None,
    herd_evidence: dict[str, list[EvidenceInput]] | None = None,
    interpreter_output: InterpreterOutput | None = None,
    archivist_output: ArchivistOutput | None = None,
    settings: LoreSettings | None = None,
) -> Fixture:
    """Build an Orchestrator with stubs.

    Returns a Fixture with all stubs accessible for assertions.
    """
    embedder = StubEmbedder()
    interpreter = StubCompletion(interpreter_output or make_interpreter_output())
    archivist = StubCompletion(
        archivist_output or ArchivistOutput(reasoning="test reasoning", answer="answer")
    )

    request_store = StubRequests()
    hypotheses = StubHypotheses(search_results=search_results)
    attestations = StubAttestations(
        by_hypotheses=by_hypotheses,
        trust_alignments=trust_alignments,
        herd_evidence=herd_evidence,
    )
    repos = Repositories(
        hypotheses=hypotheses,
        attestations=attestations,
        requests=request_store,
        cache=StubCache(),
    )
    pool = StubPool(repos)
    providers = Providers(
        embedder=embedder,
        interpreter=interpreter,
        archivist=archivist,
    )

    orchestrator = Orchestrator(
        pool=pool,
        providers=providers,
        math=make_math(),
        settings=settings or make_settings(),
    )
    return Fixture(
        orchestrator=orchestrator,
        embedder=embedder,
        interpreter=interpreter,
        archivist=archivist,
        requests=request_store,
        hypotheses=hypotheses,
        attestations=attestations,
        pool=pool,
    )


class _InternalModel(BaseModel):
    value: int


def raise_internal_validation_error(*_args: object, **_kwargs: object) -> NoReturn:
    """Stand-in constructor that fails with a real pydantic ValidationError.

    Monkeypatched over an orchestrator-internal model class to exercise the
    ``DomainInvariantError`` masking contract.
    """
    _InternalModel.model_validate({"value": "not an int"})
    raise AssertionError("model_validate should have raised")


def write_request(
    confidence: float | None = 0.7,
    hypothesis: str = "Service X uses gRPC",
    question: str = "What protocol does X use?",
) -> ConsultLoreRequest:
    return ConsultLoreRequest(
        question=question,
        hypothesis=hypothesis,
        confidence=confidence,
    )


@contextmanager
def instrumented(
    *,
    search_results: list[HypothesisResult] | None = None,
    by_hypotheses: dict[str, list[AttestationRecord]] | None = None,
    trust_alignments: list[TrustSignal] | None = None,
    interpreter_output: InterpreterOutput | None = None,
    archivist_output: ArchivistOutput | None = None,
    settings: LoreSettings | None = None,
) -> Generator[tuple[Fixture, InMemorySpanExporter, list[EventDict]]]:
    """Build an orchestrator with in-memory span export and structlog event capture.

    Installs an SDK ``TracerProvider`` and patches ``get_tracer_provider`` so
    ``start_span`` resolves through it for the block's lifetime.

    ``structlog.testing.capture_logs`` swaps in a recording wrapper for the
    block's lifetime; bound context (``oracle_id``, ``path``) shows up in each
    captured event dict via the contextvars merge processor.
    """
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    try:
        with (
            patch(
                "lore.telemetry.otel_trace.get_tracer_provider",
                return_value=tracer_provider,
            ),
            # Prepend merge_contextvars so bound context (oracle_id, path from
            # start_span) survives capture_logs' processor replacement and
            # appears in every event dict.
            structlog.testing.capture_logs(
                processors=[structlog.contextvars.merge_contextvars],
            ) as cap,
        ):
            fixture = make_orchestrator(
                search_results=search_results,
                by_hypotheses=by_hypotheses,
                trust_alignments=trust_alignments,
                interpreter_output=interpreter_output,
                archivist_output=archivist_output,
                settings=settings,
            )
            yield fixture, span_exporter, cap
    finally:
        tracer_provider.shutdown()
