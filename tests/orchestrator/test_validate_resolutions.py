"""Tests for the Archivist resolution validator.

The orchestrator runs two trust-boundary checks against the Archivist's
output before any downstream stage:

- Set-membership. Every ``corroborates`` and ``contradicts`` ID must
  appear in the retrieved set. Hallucinated UUIDs are the one foreign
  body the math has no way to ground. Other classification noise (a
  paraphrase labeled novel, an orthogonal claim labeled contradiction)
  is absorbed by trust discounting, ECBF, and decay.
- One resolution per proposition. The Archivist prompt commits to one
  resolution per inbound proposition. Over-count is the cost-DoS vector:
  each spurious ``contributes`` would fan out into the embed pipeline.

This file exercises:

- ``corroborates`` and ``contradicts`` IDs must appear in the retrieved
  set: hallucinations are rejected.
- Real retrieved IDs are accepted regardless of cosine proximity. The
  Archivist saw the IDs in its input; semantic disagreement with the
  proximity ranking is not the validator's concern.
- Resolution count must not exceed proposition count; under-count is a
  quality issue and is accepted.
- The validator runs on every consult, read and write: an Archivist
  that returns resolutions on a read is misbehaving even though nothing
  would be persisted.
"""

import importlib.resources
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel

from lore.adapter import LimitsConfig
from lore.config import LoreSettings
from lore.domain import (
    ArchivistOutput,
    ArchivistResolutionError,
    ConsultLoreRequest,
    InterpreterOutput,
    Resolution,
    TrustSignal,
)
from lore.math import EpistemicsConfig, MathService
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
# Shared stubs: minimal shapes that satisfy the orchestrator's Protocols
# ---------------------------------------------------------------------------

_STUB_EMBEDDING: list[float] = [0.01, 0.02, 0.03]


class _StubEmbedder:
    async def embed(self, text: str, *, task_type_key: TaskTypeKey | None = None) -> list[float]:
        del text, task_type_key
        return list(_STUB_EMBEDDING)


class _StubCompletion:
    def __init__(self, output: BaseModel) -> None:
        self._output = output

    async def complete[T: BaseModel](self, *, response_model: type[T], system: str, user: str) -> T:
        del response_model, system, user
        return cast(T, self._output)


class _CandidatesHypotheses:
    """Returns the configured candidate set on retrieval search.

    The validator no longer calls search: IDs are read from the retrieved
    (enriched) set the Archivist saw. This stub serves the upstream
    retrieval stage; the validator reads from its output transitively.
    """

    def __init__(self, candidates: list[HypothesisResult]) -> None:
        self._candidates = candidates

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
        return list(self._candidates)


class _NoopAttestations:
    async def append(self, record: AttestationRecord) -> None:
        del record

    async def find_by_hypothesis(self, hypothesis_id: str) -> list[AttestationRecord]:
        del hypothesis_id
        return []

    async def find_by_hypotheses(
        self, hypothesis_ids: Sequence[str]
    ) -> dict[str, list[AttestationRecord]]:
        return {h: [] for h in hypothesis_ids}

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


_: type[RepositoryPool] = _StubPool


def _bundled_prompt(name: str) -> Path:
    return Path(str(importlib.resources.files("lore.prompts").joinpath(f"{name}.md")))


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
        limits=LimitsConfig(question=10000, hypothesis=10000, context=10000, reasoning=10000),
        retrieval=RetrievalConfig(
            proximity=0.5, authority=0.5, limit=10, fan_out=2, max_keywords=1000
        ),
        postgres=PostgresConfig(min_size=1, max_size=20, timeout=10.0, max_waiting=50),
        prompts=PromptsConfig(
            scribe=_bundled_prompt("scribe"),
            consult=_bundled_prompt("consult"),
            interpreter=_bundled_prompt("interpreter"),
            archivist=_bundled_prompt("archivist"),
        ),
    )


def _make_orchestrator(
    *,
    candidates: list[HypothesisResult],
    archivist_output: ArchivistOutput,
    propositions: list[str] | None = None,
) -> Orchestrator:
    hypotheses = _CandidatesHypotheses(candidates)
    repos = Repositories(
        hypotheses=hypotheses, attestations=_NoopAttestations(), requests=_NoopRequests()
    )
    pool = _StubPool(repos)
    providers = Providers(
        embedder=_StubEmbedder(),
        interpreter=_StubCompletion(
            InterpreterOutput(
                question="normalized question",
                propositions=["prop A"] if propositions is None else propositions,
                keywords=["kw1"],
            )
        ),
        archivist=_StubCompletion(archivist_output),
    )

    return Orchestrator(
        pool=pool,
        providers=providers,
        math=MathService(c_half_life=86400.0, maturity_k=1.0, t_half_life=86400.0),
        settings=_make_settings(),
    )


def _result(id: str, *, score: float = 0.5, proximity: float = 0.95) -> HypothesisResult:
    return HypothesisResult.model_construct(
        id=id, content="some content", created_at=0, score=score, proximity=proximity
    )


def _write_request() -> ConsultLoreRequest:
    return ConsultLoreRequest(question=None, hypothesis="proposition A", confidence=0.5)


_IDENTITY = "oracle-1"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidateResolutions:
    async def test_validator_rejects_id_not_in_retrieved_set(self) -> None:
        retrieved = "00000000-0000-0000-0000-000000000aa1"
        hallucinated = "00000000-0000-0000-0000-00000000beef"
        orchestrator = _make_orchestrator(
            candidates=[_result(retrieved)],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[Resolution(corroborates=hallucinated)],
            ),
        )
        with pytest.raises(ArchivistResolutionError, match="hallucinated"):
            await orchestrator.consult(
                oracle_id=_IDENTITY, request=_write_request(), correlation_id="corr-1"
            )

    async def test_validator_accepts_id_in_retrieved_set_regardless_of_proximity(self) -> None:
        """A retrieved ID is accepted even when its proximity ranking is low.

        The Archivist saw the ID in its enriched input. The validator's
        only job is to reject IDs the Archivist could not have legitimately
        seen; semantic disagreement with the proximity ranking is not
        the validator's concern. This is the regression guard for the
        cross-task-type cosine asymmetry that broke the e2e suite under
        the old proximity floor.
        """
        retrieved = "00000000-0000-0000-0000-000000000aa2"
        orchestrator = _make_orchestrator(
            # Proximity 0.5: would have failed the old 0.9 floor.
            candidates=[_result(retrieved, proximity=0.5)],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[Resolution(corroborates=retrieved)],
            ),
        )
        # No exception: recorder is noop in these stubs, so consult returns.
        await orchestrator.consult(
            oracle_id=_IDENTITY, request=_write_request(), correlation_id="corr-1"
        )

    async def test_validator_rejects_contradicts_id_not_in_retrieved_set(self) -> None:
        retrieved = "00000000-0000-0000-0000-000000000aa3"
        hallucinated = "00000000-0000-0000-0000-00000000beef"
        orchestrator = _make_orchestrator(
            candidates=[_result(retrieved)],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[
                    Resolution(contributes="novel claim", contradicts=[hallucinated]),
                ],
            ),
        )
        with pytest.raises(ArchivistResolutionError, match="hallucinated"):
            await orchestrator.consult(
                oracle_id=_IDENTITY, request=_write_request(), correlation_id="corr-1"
            )

    async def test_validator_accepts_contradicts_id_with_low_proximity(self) -> None:
        """A retrieved contradicts ID is accepted at any proximity.

        Contradicting claims are typically on-topic but vary widely in
        surface form: a contradiction can sit at proximity 0.2 against
        the inbound and still be a legitimate contradiction. The
        validator imposes no cosine threshold.
        """
        ok = "00000000-0000-0000-0000-000000000aa4"
        contradicted = "00000000-0000-0000-0000-000000000aa5"
        orchestrator = _make_orchestrator(
            candidates=[
                _result(ok, proximity=0.95),
                _result(contradicted, proximity=0.2),
            ],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[Resolution(corroborates=ok, contradicts=[contradicted])],
            ),
        )
        # No exception: low proximity is fine because the ID was retrieved.
        await orchestrator.consult(
            oracle_id=_IDENTITY, request=_write_request(), correlation_id="corr-1"
        )

    async def test_validator_inspects_every_resolution_in_the_list(self) -> None:
        """A hallucinated ID in a later resolution still raises.

        IDEA §Stage 4: a single ``consult`` call may produce multiple
        writes. The validator iterates the full list (a regression that
        bails after the first resolution would let the second one's
        hallucinated ID through to the recorder).
        """
        ok = "00000000-0000-0000-0000-000000000aa6"
        hallucinated = "00000000-0000-0000-0000-00000000beef"
        orchestrator = _make_orchestrator(
            candidates=[_result(ok)],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[
                    Resolution(corroborates=ok),
                    Resolution(contributes="novel", contradicts=[hallucinated]),
                ],
            ),
            propositions=["prop A", "prop B"],
        )
        with pytest.raises(ArchivistResolutionError, match="hallucinated"):
            await orchestrator.consult(
                oracle_id=_IDENTITY, request=_write_request(), correlation_id="corr-1"
            )

    async def test_validator_rejects_hallucinated_id_on_read_path(self) -> None:
        """A hallucinated ID is raised even when ``confidence`` is None.

        The validator runs on every consult: an Archivist returning IDs
        the retrieved set never contained is misbehaving regardless of
        whether the resolution would be persisted. Catching it on reads
        too means the same signal surfaces in the same place.
        """
        retrieved = "00000000-0000-0000-0000-000000000aa7"
        hallucinated = "00000000-0000-0000-0000-00000000beef"
        orchestrator = _make_orchestrator(
            candidates=[_result(retrieved)],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[Resolution(corroborates=hallucinated)],
            ),
        )
        read_request = ConsultLoreRequest(question="some question", confidence=None)
        with pytest.raises(ArchivistResolutionError, match="hallucinated"):
            await orchestrator.consult(
                oracle_id=_IDENTITY, request=read_request, correlation_id="corr-1"
            )

    async def test_validator_rejects_overflow_resolutions(self) -> None:
        """More resolutions than propositions violates the one-per-proposition contract.

        The Archivist prompt commits to one resolution per inbound
        proposition. Over-count is the cost-DoS vector: each spurious
        ``contributes`` would fan out into the embed pipeline. The bound
        is derived from inputs the orchestrator already has; no guessed
        cap is needed.
        """
        retrieved = "00000000-0000-0000-0000-000000000ab1"
        orchestrator = _make_orchestrator(
            candidates=[_result(retrieved)],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[
                    Resolution(corroborates=retrieved),
                    Resolution(contributes="extra one"),
                ],
            ),
            propositions=["prop A"],
        )
        with pytest.raises(ArchivistResolutionError, match=r"2 resolutions for 1 propositions"):
            await orchestrator.consult(
                oracle_id=_IDENTITY, request=_write_request(), correlation_id="corr-1"
            )

    async def test_validator_accepts_equal_resolution_and_proposition_count(self) -> None:
        first = "00000000-0000-0000-0000-000000000ab2"
        second = "00000000-0000-0000-0000-000000000ab5"
        orchestrator = _make_orchestrator(
            candidates=[_result(first), _result(second)],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[
                    Resolution(corroborates=first),
                    Resolution(corroborates=second),
                ],
            ),
            propositions=["prop A", "prop B"],
        )
        await orchestrator.consult(
            oracle_id=_IDENTITY, request=_write_request(), correlation_id="corr-1"
        )

    async def test_validator_accepts_under_count_resolutions(self) -> None:
        """Fewer resolutions than propositions is a quality issue, not safety: accepted."""
        retrieved = "00000000-0000-0000-0000-000000000ab3"
        orchestrator = _make_orchestrator(
            candidates=[_result(retrieved)],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[Resolution(corroborates=retrieved)],
            ),
            propositions=["prop A", "prop B"],
        )
        await orchestrator.consult(
            oracle_id=_IDENTITY, request=_write_request(), correlation_id="corr-1"
        )

    async def test_validator_rejects_resolutions_when_no_propositions(self) -> None:
        """Zero propositions with any resolutions is an overflow violation.

        The Archivist prompt explicitly says: if no hypothesis is present,
        leave resolutions empty. This is the regression guard that proves
        the validator runs on the read path: a read consult has zero
        propositions and any resolution at all is misbehavior.
        """
        retrieved = "00000000-0000-0000-0000-000000000ab4"
        orchestrator = _make_orchestrator(
            candidates=[_result(retrieved)],
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[Resolution(corroborates=retrieved)],
            ),
            propositions=[],
        )
        read_request = ConsultLoreRequest(question="some question", confidence=None)
        with pytest.raises(ArchivistResolutionError, match=r"1 resolutions for 0 propositions"):
            await orchestrator.consult(
                oracle_id=_IDENTITY, request=read_request, correlation_id="corr-1"
            )
