"""Fan-out and deduplication tests — multiple search calls + retrieval shape."""

import json
from collections.abc import Sequence

import pytest

from lore.config import LoreSettings
from lore.domain import (
    ArchivistOutput,
    ConsultLoreRequest,
    InterpreterOutput,
)
from lore.orchestrator import Orchestrator
from lore.providers import Providers, TaskTypeKey
from lore.repositories import (
    HypothesisResult,
    Repositories,
    RetrievalConfig,
)
from tests.orchestrator.conftest import (
    STUB_EMBEDDING,
    StubAttestations,
    StubCompletion,
    StubEmbedder,
    StubHypotheses,
    StubPool,
    StubRequests,
    make_hypothesis_result,
    make_math,
    make_orchestrator,
    make_settings,
)


class _SequentialSearchHypotheses(StubHypotheses):
    """Hypothesis stub that returns different results per search() call.

    Each call pops the next result set from the front of the list.
    Falls back to empty when exhausted.
    """

    def __init__(self, search_result_sets: list[list[HypothesisResult]]) -> None:
        super().__init__()
        self._result_sets = list(search_result_sets)
        self.search_calls: list[tuple[Sequence[float], str, int]] = []

    async def search(
        self,
        *,
        embedding: Sequence[float],
        query: str,
        weights: tuple[float, float],
        limit: int,
        fan_out: int,
    ) -> list[HypothesisResult]:
        self.search_calls.append((embedding, query, fan_out))
        if self._result_sets:
            return self._result_sets.pop(0)
        return []


class TestFanOutEmbedsAllPropositionsInParallel:
    async def test_fan_out_embeds_all_propositions_in_parallel(self) -> None:
        fixture = make_orchestrator(
            interpreter_output=InterpreterOutput(
                question="normalized question",
                propositions=["prop A", "prop B", "prop C"],
                keywords=["kw1"],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        # question + 3 propositions = 4 embed calls
        # question text → "question"; propositions → "verification"
        question_embeds = [(text, key) for text, key in fixture.embedder.calls if key == "question"]
        verification_embeds = [
            (text, key) for text, key in fixture.embedder.calls if key == "verification"
        ]
        assert len(question_embeds) == 1
        assert len(verification_embeds) == 3
        assert question_embeds[0][0] == "normalized question"
        verification_texts = {text for text, _ in verification_embeds}
        assert verification_texts == {"prop A", "prop B", "prop C"}


class TestRetrieveTaskTypeBySource:
    async def test_retrieve_uses_question_key_for_question_and_verification_key_for_propositions(
        self,
    ) -> None:
        fixture = make_orchestrator(
            interpreter_output=InterpreterOutput(
                question="normalized question text",
                propositions=["proposition alpha", "proposition beta"],
                keywords=["kw1"],
            ),
        )

        # Read-only request: no confidence → no write-path document embeddings.
        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        question_calls = [(text, key) for text, key in fixture.embedder.calls if key == "question"]
        verification_calls = [
            (text, key) for text, key in fixture.embedder.calls if key == "verification"
        ]

        assert question_calls == [("normalized question text", "question")]
        assert sorted(verification_calls) == [
            ("proposition alpha", "verification"),
            ("proposition beta", "verification"),
        ]


class TestFanOutDeduplicatesByHypothesisId:
    async def test_fan_out_deduplicates_by_hypothesis_id(self) -> None:
        shared_id = "550e8400-e29b-41d4-a716-446655440000"
        unique_id = "660e8400-e29b-41d4-a716-446655440000"

        # Search result from question: shared hypothesis with low score
        result_low = make_hypothesis_result(id=shared_id, score=0.3)
        # Search result from proposition: shared hypothesis with high score + unique
        result_high = make_hypothesis_result(id=shared_id, score=0.8)
        result_unique = make_hypothesis_result(id=unique_id, score=0.5)

        hypotheses = _SequentialSearchHypotheses(
            [
                [result_low],  # question search
                [result_high, result_unique],  # proposition search
            ]
        )

        embedder = StubEmbedder()
        interpreter = StubCompletion(
            InterpreterOutput(
                question="normalized question",
                propositions=["prop A"],
                keywords=["kw1"],
            )
        )
        archivist = StubCompletion(ArchivistOutput(reasoning="test reasoning", answer="answer"))

        request_store = StubRequests()
        repos = Repositories(
            hypotheses=hypotheses,
            attestations=StubAttestations(),
            requests=request_store,
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
            settings=make_settings(),
        )

        await orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        # Archivist should see 2 results: shared (deduped with high score) + unique
        assert len(archivist.calls) == 1
        _system, user_text = archivist.calls[0]
        payload = json.loads(user_text)
        retrieved = payload["retrieved"]
        assert len(retrieved) == 2
        ids = {r["id"] for r in retrieved}
        assert ids == {shared_id, unique_id}
        # The shared hypothesis should have the higher score
        shared = next(r for r in retrieved if r["id"] == shared_id)
        assert shared["score"] == 0.8


class TestFanOutEmbeddingFailureFailsEntireRequest:
    async def test_fan_out_embedding_failure_fails_entire_request(self) -> None:

        class _FailingEmbedder:
            """Fails on the second embed call."""

            def __init__(self) -> None:
                self._call_count = 0

            async def embed(
                self, text: str, *, task_type_key: TaskTypeKey | None = None
            ) -> list[float]:
                self._call_count += 1
                if self._call_count >= 2:
                    raise RuntimeError("embedding service down")
                return STUB_EMBEDDING

        embedder = _FailingEmbedder()
        interpreter = StubCompletion(
            InterpreterOutput(
                question="normalized question",
                propositions=["prop A"],
                keywords=["kw1"],
            )
        )
        archivist = StubCompletion(ArchivistOutput(reasoning="test reasoning", answer="answer"))

        repos = Repositories(
            hypotheses=StubHypotheses(),
            attestations=StubAttestations(),
            requests=StubRequests(),
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
            settings=make_settings(),
        )

        with pytest.raises(RuntimeError, match="embedding service down"):
            await orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )


class TestFanOutSearchPerEmbedding:
    async def test_fan_out_search_per_embedding(self) -> None:
        hypotheses = _SequentialSearchHypotheses([[], [], []])

        embedder = StubEmbedder()
        interpreter = StubCompletion(
            InterpreterOutput(
                question="normalized question",
                propositions=["prop A", "prop B"],
                keywords=["kw1"],
            )
        )
        archivist = StubCompletion(ArchivistOutput(reasoning="test reasoning", answer="answer"))

        request_store = StubRequests()
        repos = Repositories(
            hypotheses=hypotheses,
            attestations=StubAttestations(),
            requests=request_store,
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
            settings=make_settings(),
        )

        await orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        # 3 search calls: question + prop A + prop B
        assert len(hypotheses.search_calls) == 3


class TestMaxKeywordsTruncation:
    async def test_keywords_truncated_to_max(self) -> None:
        hypotheses = _SequentialSearchHypotheses([[]])

        embedder = StubEmbedder()
        interpreter = StubCompletion(
            InterpreterOutput(
                question="normalized question",
                keywords=["kw1", "kw2", "kw3", "kw4", "kw5"],
            )
        )
        archivist = StubCompletion(ArchivistOutput(reasoning="r", answer="a"))

        repos = Repositories(
            hypotheses=hypotheses,
            attestations=StubAttestations(),
            requests=StubRequests(),
        )
        base = make_settings()
        settings = LoreSettings.model_construct(
            dsn=base.dsn,
            oidc=base.oidc,
            epistemics=base.epistemics,
            embedding=base.embedding,
            fast=base.fast,
            reasoning=base.reasoning,
            limits=base.limits,
            retrieval=RetrievalConfig(
                proximity=0.5,
                authority=0.5,
                limit=10,
                fan_out=2,
                max_keywords=2,
            ),
            server=base.server,
            prompts=base.prompts,
        )
        orchestrator = Orchestrator(
            pool=StubPool(repos),
            providers=Providers(embedder=embedder, interpreter=interpreter, archivist=archivist),
            math=make_math(),
            settings=settings,
        )

        await orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        assert len(hypotheses.search_calls) == 1
        _, query, _ = hypotheses.search_calls[0]
        assert query == "kw1 kw2"


class TestOrchestratorForwardsFanOutToSearch:
    async def test_orchestrator_forwards_fan_out_to_search(self) -> None:
        hypotheses = _SequentialSearchHypotheses([[], [], []])
        embedder = StubEmbedder()
        interpreter = StubCompletion(
            InterpreterOutput(
                question="normalized question",
                propositions=["prop A", "prop B"],
                keywords=["kw1"],
            )
        )
        archivist = StubCompletion(ArchivistOutput(reasoning="r", answer="a"))

        repos = Repositories(
            hypotheses=hypotheses,
            attestations=StubAttestations(),
            requests=StubRequests(),
        )
        base = make_settings()
        # ``fan_out=7`` is arbitrary but deliberately distinct from the
        # default ``2`` so a regression to the hardcoded value would fail.
        settings = LoreSettings.model_construct(
            dsn=base.dsn,
            oidc=base.oidc,
            epistemics=base.epistemics,
            embedding=base.embedding,
            fast=base.fast,
            reasoning=base.reasoning,
            limits=base.limits,
            retrieval=RetrievalConfig(
                proximity=0.5,
                authority=0.5,
                limit=10,
                fan_out=7,
                max_keywords=10,
            ),
            server=base.server,
            prompts=base.prompts,
        )
        orchestrator = Orchestrator(
            pool=StubPool(repos),
            providers=Providers(embedder=embedder, interpreter=interpreter, archivist=archivist),
            math=make_math(),
            settings=settings,
        )

        await orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        # Every search call (question + each proposition) carries the
        # configured fan_out value.
        assert len(hypotheses.search_calls) == 3
        observed_fan_outs = [fan_out for _, _, fan_out in hypotheses.search_calls]
        assert observed_fan_outs == [7, 7, 7]
