"""Write-path orchestrator tests: hypothesis-carrying consult calls.

Transfer-specific tests live in ``test_transfer.py``.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from lore.domain import (
    ArchivistOutput,
    ConsultLoreRequest,
    EvidenceInput,
    InferenceError,
    Resolution,
    TrustSignal,
)
from lore.orchestrator import Orchestrator
from lore.prompts import load_prompt
from lore.providers import Providers
from lore.repositories import (
    DecayWindow,
    LedgerView,
    Repositories,
)
from tests.orchestrator.conftest import (
    STUB_EMBEDDING,
    StubAttestations,
    StubCache,
    StubCompletion,
    StubEmbedder,
    StubHypotheses,
    StubPool,
    StubRequests,
    make_attestation,
    make_hypothesis_result,
    make_interpreter_output,
    make_math,
    make_orchestrator,
    make_settings,
    write_request,
)


class TestWritePathPersistsStructuredRequest:
    async def test_write_path_persists_structured_request_all_fields(self) -> None:
        fixture = make_orchestrator()

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(
                question="What protocol does X use?",
                context="investigating the wire format",
                hypothesis="Service X uses gRPC",
                reasoning="grep found grpc imports",
                confidence=0.7,
            ),
            correlation_id="corr-xyz",
        )

        assert len(fixture.requests.stored) == 1
        stored = fixture.requests.stored[0]
        assert stored.id == "corr-xyz"
        assert stored.oracle_id == "oracle-1"
        assert stored.question == "What protocol does X use?"
        assert stored.context == "investigating the wire format"
        assert stored.hypothesis == "Service X uses gRPC"
        assert stored.reasoning == "grep found grpc imports"
        assert stored.confidence == 0.7

    async def test_write_path_persists_sparse_request_fields(self) -> None:
        fixture = make_orchestrator()

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(hypothesis="atomic claim", confidence=0.4),
            correlation_id="corr-sparse",
        )

        assert len(fixture.requests.stored) == 1
        stored = fixture.requests.stored[0]
        assert stored.hypothesis == "atomic claim"
        assert stored.confidence == 0.4
        assert stored.question is None
        assert stored.context is None
        assert stored.reasoning is None

    async def test_write_path_multiple_attestations_share_single_request_row(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Three claims recorded.",
                resolutions=[
                    Resolution(contributes="atomic A"),
                    Resolution(contributes="atomic B"),
                    Resolution(contributes="atomic C"),
                ],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-one-call",
        )

        # Three attestations, all sharing the same correlation_id.
        assert len(fixture.attestations.appended) == 3
        assert all(c.correlation_id == "corr-one-call" for c in fixture.attestations.appended)
        # Exactly one request row written for the call.
        assert len(fixture.requests.stored) == 1
        assert fixture.requests.stored[0].id == "corr-one-call"


class TestWritePathStoresNovelHypothesis:
    async def test_write_path_novel_no_relations(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Noted.",
                resolutions=[Resolution(contributes="atomic claim A")],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        # Novel hypothesis was stored
        assert len(fixture.hypotheses.stored) == 1
        content, embedding, _created_at = fixture.hypotheses.stored[0]
        assert content == "atomic claim A"
        assert embedding == STUB_EMBEDDING

        # Initial attestation was appended for the novel hypothesis
        assert len(fixture.attestations.appended) == 1
        call = fixture.attestations.appended[0]
        assert call.oracle_id == "oracle-1"
        assert call.correlation_id == "corr-1"
        assert call.c_oracle_raw == 0.7


class TestWritePathIdentityMatchAttestsExisting:
    async def test_write_path_identity_match_attests_existing(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=hypothesis_id, content="existing claim")
        attestation = make_attestation(hypothesis_id=hypothesis_id)

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={hypothesis_id: [attestation]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Agreed.",
                resolutions=[Resolution(corroborates=hypothesis_id)],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        # No new hypothesis stored
        assert len(fixture.hypotheses.stored) == 0

        # One attestation appended to the existing hypothesis
        assert len(fixture.attestations.appended) == 1
        call = fixture.attestations.appended[0]
        assert call.hypothesis_id == hypothesis_id
        assert call.c_oracle_raw == 0.7  # positive (identity match)


class TestWritePathContributeNegativeConfidence:
    async def test_contribute_negative_confidence(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Disbelief in a novel.",
                resolutions=[Resolution(contributes="dubious claim")],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=-0.4),
            correlation_id="corr-1",
        )

        # Single oracle attestation on the novel with negative c_oracle_raw.
        assert len(fixture.attestations.appended) == 1
        call = fixture.attestations.appended[0]
        assert call.oracle_id == "oracle-1"
        assert call.c_oracle_raw == -0.4


class TestRecordUsesPostTransactionAttestationState:
    """Recorder's `c_herd` reflects attestations as they exist at transaction time.

    A competing attestation that lands between the read-path snapshot and the
    write transaction must be visible to the Recorder. The test stubs returns
    different maps for the read-path call (empty) and the in-tx call (one prior
    attestation) and asserts the recorded `c_herd` matches the in-tx scenario,
    not the read-path one.
    """

    async def test_record_uses_post_transaction_attestation_state(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        prior = make_attestation(
            hypothesis_id=hypothesis_id, oracle_id="oracle-2", c_oracle_discounted=0.3
        )

        class _SwitchingAttestations(StubAttestations):
            """First call (read path): empty. Subsequent calls (write tx): one prior."""

            def __init__(self) -> None:
                super().__init__()
                self._call_count = 0

            async def find_by_hypotheses(
                self,
                hypothesis_ids: Sequence[str],
                *,
                window: DecayWindow | None = None,
            ) -> dict[str, LedgerView]:
                self._call_count += 1
                if self._call_count == 1:
                    return {
                        hid: LedgerView(rows=[], attestation_count=0, last_attested=None)
                        for hid in hypothesis_ids
                    }
                return {
                    hid: LedgerView(
                        rows=[prior], attestation_count=1, last_attested=prior.timestamp
                    )
                    for hid in hypothesis_ids
                }

        result = make_hypothesis_result(id=hypothesis_id, content="existing claim")
        embedder = StubEmbedder()
        interpreter = StubCompletion(make_interpreter_output())
        archivist = StubCompletion(
            ArchivistOutput(
                reasoning="test reasoning",
                answer="Agreed.",
                resolutions=[Resolution(corroborates=hypothesis_id)],
            )
        )
        request_store = StubRequests()
        hypotheses = StubHypotheses(search_results=[result])
        attestations = _SwitchingAttestations()
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
            settings=make_settings(),
        )

        await orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        assert len(attestations.appended) == 1
        recorded = attestations.appended[0]

        # Compute what c_herd would be under each snapshot. The Recorder must
        # match the in-tx scenario.
        math = make_math()
        from lore.domain import EvidenceInput

        in_tx_evidence = [
            EvidenceInput(c_oracle_discounted=prior.c_oracle_discounted, timestamp=prior.timestamp)
        ]
        expected_in_tx = math.prepare_attestation(
            confidence=0.7,
            existing=in_tx_evidence,
            t_now=recorded.timestamp,
            t_oracle=recorded.t_oracle,
            n_oracle_prior=1,
        )
        expected_read_path = math.prepare_attestation(
            confidence=0.7,
            existing=[],
            t_now=recorded.timestamp,
            t_oracle=recorded.t_oracle,
            n_oracle_prior=0,
        )

        assert abs(recorded.c_herd - expected_in_tx.c_herd) < 1e-10
        assert abs(recorded.c_herd - expected_read_path.c_herd) > 1e-6


class TestWritePathEmbedsNovelsWithDocumentTaskType:
    async def test_write_path_embeds_novels_with_document_task_type(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Noted.",
                resolutions=[Resolution(contributes="novel claim X")],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        # Find the embed call for the novel proposition
        document_calls = [(text, key) for text, key in fixture.embedder.calls if key == "document"]
        assert len(document_calls) == 1
        assert document_calls[0][0] == "novel claim X"


class TestWritePathComputesTrustAndMaturity:
    async def test_write_path_computes_trust_and_maturity(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Noted.",
                resolutions=[Resolution(contributes="novel claim")],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        # Trust was computed (attestation has t_oracle field)
        assert len(fixture.attestations.appended) >= 1
        call = fixture.attestations.appended[0]
        # Cold start oracle with no history -> t_oracle = 0.5
        assert call.t_oracle == 0.5


class TestWritePathReadThenWrite:
    async def test_write_path_read_then_write(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="The herd now believes Y.",
                resolutions=[Resolution(contributes="novel Y")],
            ),
        )

        response = await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        # Answer comes from the archivist
        assert response.answer == "The herd now believes Y."

        # Interpreter was called (shared pipeline)
        assert len(fixture.interpreter.calls) == 1

        # Embedder was called at least twice: question + novel
        assert len(fixture.embedder.calls) >= 2


class TestWritePathVacuousConfidence:
    """Oracle submits confidence=0.0: vacuous, not absent."""

    async def test_write_path_vacuous_confidence_produces_zero_raw(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Noted.",
                resolutions=[Resolution(contributes="vacuous claim")],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.0),
            correlation_id="corr-1",
        )

        assert len(fixture.attestations.appended) == 1
        call = fixture.attestations.appended[0]
        assert call.c_oracle_raw == 0.0
        assert call.c_oracle_discounted == 0.0


class TestWritePathNonColdStartTrust:
    async def test_write_path_trust_from_alignment_history(self) -> None:
        # Oracle who agreed perfectly with the herd on a prior attestation.
        # Another oracle's evidence witnesses the hypothesis; an unwitnessed
        # row would leave the trust scan and yield base rate exactly.
        alignment = TrustSignal(
            hypothesis_id="hyp-1",
            c_oracle_raw=0.6,
            timestamp=2000000000,
            c_herd_prior=0.6,
            n_oracle_prior=0,
        )

        fixture = make_orchestrator(
            trust_alignments=[alignment],
            herd_evidence={"hyp-1": [EvidenceInput(c_oracle_discounted=0.6, timestamp=2000000000)]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Noted.",
                resolutions=[Resolution(contributes="novel claim")],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        assert len(fixture.attestations.appended) == 1
        call = fixture.attestations.appended[0]
        # Perfect alignment history should produce trust > 0.5
        assert call.t_oracle > 0.5


class TestWritePathOrthogonalProducesNoWrites:
    async def test_write_path_orthogonal_produces_no_writes(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Nothing relevant found.",
            ),
        )

        response = await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        assert response.answer == "Nothing relevant found."
        assert len(fixture.hypotheses.stored) == 0
        assert len(fixture.attestations.appended) == 0


class TestWritePathMultipleNovels:
    async def test_write_path_stores_all_novels_with_distinct_ids(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Three new claims recorded.",
                resolutions=[
                    Resolution(contributes="atomic A"),
                    Resolution(contributes="atomic B"),
                    Resolution(contributes="atomic C"),
                ],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        # All 3 novels stored
        assert len(fixture.hypotheses.stored) == 3
        stored_contents = {content for content, _, _ in fixture.hypotheses.stored}
        assert stored_contents == {"atomic A", "atomic B", "atomic C"}

        # Each gets its own attestation
        assert len(fixture.attestations.appended) == 3
        attested_ids = {call.hypothesis_id for call in fixture.attestations.appended}
        assert len(attested_ids) == 3  # distinct IDs

        # Embedder was called with "document" task type for each novel
        document_calls = [(text, key) for text, key in fixture.embedder.calls if key == "document"]
        assert len(document_calls) == 3
        embedded_texts = {text for text, _ in document_calls}
        assert embedded_texts == {"atomic A", "atomic B", "atomic C"}


class _RaisingCompletion:
    """Completer stub that raises on every ``complete()`` call.

    Mirrors the shape of ``StubCompletion`` in conftest, but blows up
    instead of returning a fixed response. Used to simulate the Interpreter
    (or any downstream completer) erroring after the request row is stored.
    """

    async def complete[T: BaseModel](self, *, response_model: type[T], system: str, user: str) -> T:
        raise InferenceError("interpreter down")


class TestWritePathOrphanRequestRowOnInterpreterFailure:
    """Regression guard: a consult that fails after the request row is stored
    leaves the row in place with zero joining attestations.

    This is the architecture's documented provenance contract: "storage is
    cheap, information is valuable". The orchestrator writes the request row
    autocommit *before* any provider call runs; an Interpreter failure
    cannot affect that row. The test locks the ordering in so future refactors
    can't quietly move the request store after the first provider call.
    """

    async def test_interpreter_failure_leaves_request_row_with_zero_attestations_and_propagates(
        self,
    ) -> None:
        embedder = StubEmbedder()
        interpreter = _RaisingCompletion()
        archivist = StubCompletion(ArchivistOutput(reasoning="unused", answer="unused"))
        request_store = StubRequests()
        hypotheses = StubHypotheses()
        attestations = StubAttestations()
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
            settings=make_settings(),
        )

        with pytest.raises(InferenceError, match="interpreter down"):
            await orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(
                    question="What protocol does X use?",
                    context="investigating",
                    hypothesis="Service X uses gRPC",
                    reasoning="grep found grpc imports",
                    confidence=0.7,
                ),
                correlation_id="corr-orphan",
            )

        # (a) The request row was stored verbatim before the Interpreter ran.
        assert len(request_store.stored) == 1
        stored = request_store.stored[0]
        assert stored.id == "corr-orphan"
        assert stored.oracle_id == "oracle-1"
        assert stored.question == "What protocol does X use?"
        assert stored.context == "investigating"
        assert stored.hypothesis == "Service X uses gRPC"
        assert stored.reasoning == "grep found grpc imports"
        assert stored.confidence == 0.7

        # (b) No attestations joined to that correlation_id: provenance only.
        assert not any(call.correlation_id == "corr-orphan" for call in attestations.appended)


class TestArchivistInputCarriesConsultDate:
    async def test_archivist_input_carries_consult_date(self) -> None:
        fixture = make_orchestrator()

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        _system, user = fixture.archivist.calls[0]
        payload = json.loads(user)
        t_now = fixture.requests.stored[0].timestamp
        expected = datetime.fromtimestamp(t_now, tz=UTC).date().isoformat()
        assert payload["today"] == expected


class TestWritePathArchivistSystemPromptCarriesIncludes:
    async def test_archivist_system_prompt_includes_domain_narrative(self, tmp_path: Path) -> None:
        settings = make_settings()
        narrative = tmp_path / "narrative.md"
        narrative.write_text("DOMAIN NARRATIVE.")
        glossary = tmp_path / "glossary.md"
        glossary.write_text("GLOSSARY TERMS.")
        prompts = settings.prompts.model_copy(update={"narrative": narrative, "glossary": glossary})
        settings = settings.model_copy(update={"prompts": prompts})

        fixture = make_orchestrator(settings=settings)

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        system = fixture.archivist.calls[0][0]
        assert system.startswith("DOMAIN NARRATIVE.")
        assert "GLOSSARY TERMS." in system
        assert load_prompt(settings.prompts.archivist) in system


class TestWritePathArchivistSystemPromptDefaultsToBase:
    async def test_archivist_system_prompt_defaults_to_base_without_includes(self) -> None:
        settings = make_settings()

        fixture = make_orchestrator(settings=settings)

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(),
            correlation_id="corr-1",
        )

        assert fixture.archivist.calls[0][0] == load_prompt(settings.prompts.archivist)
