"""Read-path orchestrator tests: question-only consult calls."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore.domain import (
    ArchivistOutput,
    ConsultLoreRequest,
    StorageError,
)
from lore.orchestrator import Orchestrator
from lore.prompts import load_prompt
from lore.providers import Providers
from lore.repositories import (
    Repositories,
    RequestRecord,
)
from tests.orchestrator.conftest import (
    StubAttestations,
    StubCache,
    StubCompletion,
    StubEmbedder,
    StubHypotheses,
    StubPool,
    make_attestation,
    make_hypothesis_result,
    make_interpreter_output,
    make_math,
    make_orchestrator,
    make_settings,
)


class TestReadPathReturnsAnswer:
    async def test_read_path_returns_answer(self) -> None:
        fixture = make_orchestrator(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning", answer="The herd believes X."
            ),
        )

        response = await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        assert response.answer == "The herd believes X."


class TestReadPathCallsInterpreter:
    async def test_read_path_calls_interpreter(self) -> None:
        fixture = make_orchestrator()

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?", context="investigating Y"),
            correlation_id="corr-1",
        )

        assert len(fixture.interpreter.calls) == 1
        _system, user = fixture.interpreter.calls[0]
        assert "What is X?" in user


class TestInterpreterSystemPromptCarriesDomainIncludes:
    async def test_interpreter_system_prompt_includes_domain_narrative(
        self, tmp_path: Path
    ) -> None:
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
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        system = fixture.interpreter.calls[0][0]
        assert system.startswith("DOMAIN NARRATIVE.")
        assert "GLOSSARY TERMS." in system
        assert load_prompt(settings.prompts.interpreter) in system


class TestInterpreterSystemPromptDefaultsToBase:
    async def test_interpreter_system_prompt_defaults_to_base_without_includes(self) -> None:
        settings = make_settings()

        fixture = make_orchestrator(settings=settings)

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        assert fixture.interpreter.calls[0][0] == load_prompt(settings.prompts.interpreter)


class TestInterpreterInputCarriesConsultDate:
    async def test_interpreter_input_carries_consult_date(self) -> None:
        fixture = make_orchestrator()

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        _system, user = fixture.interpreter.calls[0]
        payload = json.loads(user)
        t_now = fixture.requests.stored[0].timestamp
        expected = datetime.fromtimestamp(t_now, tz=UTC).date().isoformat()
        assert payload["today"] == expected


class TestReadPathEmbedsNormalizedQuestion:
    async def test_read_path_embeds_normalized_question(self) -> None:
        fixture = make_orchestrator(
            interpreter_output=make_interpreter_output(question="normalized question text"),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="raw question"),
            correlation_id="corr-1",
        )

        assert len(fixture.embedder.calls) >= 1
        text, task_type_key = fixture.embedder.calls[0]
        assert text == "normalized question text"
        assert task_type_key == "question"


class TestReadPathEnrichesWithEpistemicState:
    async def test_read_path_enriches_with_epistemic_state(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=hypothesis_id)
        attestation = make_attestation(hypothesis_id=hypothesis_id, c_oracle_discounted=0.3)

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={hypothesis_id: [attestation]},
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        assert len(fixture.archivist.calls) == 1
        _system, user_text = fixture.archivist.calls[0]
        payload = json.loads(user_text)
        retrieved = payload["retrieved"][0]
        assert retrieved["attestation_count"] == 1
        assert retrieved["c_herd"] != 0.0  # attested, not vacuous


class TestReadPathArchivistPayloadLastAttested:
    async def test_archivist_payload_last_attested_is_iso_date(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=hypothesis_id)
        attestation = make_attestation(hypothesis_id=hypothesis_id, timestamp=2000000000)

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={hypothesis_id: [attestation]},
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        _system, user = fixture.archivist.calls[0]
        payload = json.loads(user)
        expected = datetime.fromtimestamp(2000000000, tz=UTC).date().isoformat()
        assert payload["retrieved"][0]["last_attested"] == expected

    async def test_archivist_payload_last_attested_null_when_never_attested(self) -> None:
        fixture = make_orchestrator(search_results=[make_hypothesis_result()])

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        _system, user = fixture.archivist.calls[0]
        payload = json.loads(user)
        assert payload["retrieved"][0]["last_attested"] is None


class TestReadPathPersistsStructuredRequest:
    async def test_read_path_persists_structured_request_all_fields(self) -> None:
        fixture = make_orchestrator()

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?", context="investigating Y"),
            correlation_id="corr-123",
        )

        assert len(fixture.requests.stored) == 1
        stored = fixture.requests.stored[0]
        assert stored.id == "corr-123"
        assert stored.oracle_id == "oracle-1"
        assert stored.question == "What is X?"
        assert stored.context == "investigating Y"
        assert stored.hypothesis is None
        assert stored.reasoning is None
        assert stored.confidence is None


class TestOracleIdLiteralPassthrough:
    """No in-process code path transforms ``oracle_id`` between adapter entry and storage.

    The README documents: "the ledger and provenance tables always store the
    raw value; redaction applies only to telemetry export." Hashing oracle_id
    in flight would silently violate this contract, the kind of mistake the
    deleted ``OracleIdentity.fingerprint`` pairing was originally meant to
    prevent. The byte-distinct marker below is what catches a future
    transformation: it survives only under literal passthrough.
    """

    async def test_oracle_id_reaches_request_store_unmodified(self) -> None:
        fixture = make_orchestrator()
        # A byte-distinct marker; any normalization (lowercase, hash, strip,
        # NFC) would change at least one character.
        marker = "Alice.Smith+lore@Example.COM"

        await fixture.orchestrator.consult(
            oracle_id=marker,
            request=ConsultLoreRequest(question="who am I?"),
            correlation_id="corr-passthrough",
        )

        assert fixture.requests.stored[0].oracle_id == marker


class TestRequestStoreErrorPropagates:
    async def test_request_store_error_propagates_as_storage_error(self) -> None:
        embedder = StubEmbedder()
        interpreter = StubCompletion(make_interpreter_output())
        archivist = StubCompletion(ArchivistOutput(reasoning="test reasoning", answer="answer"))

        class _FailingRequests:
            async def store(self, record: RequestRecord) -> None:
                raise StorageError("requests insert failed")

        repos = Repositories(
            hypotheses=StubHypotheses(),
            attestations=StubAttestations(),
            requests=_FailingRequests(),
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

        with pytest.raises(StorageError, match="requests insert failed"):
            await orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )


class TestReadPathNoResults:
    """The herd has no knowledge on this topic."""

    async def test_empty_archive_returns_archivist_answer(self) -> None:
        fixture = make_orchestrator(search_results=[])

        response = await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=ConsultLoreRequest(question="What is X?"),
            correlation_id="corr-1",
        )

        assert response.answer == "answer"


class TestReadPathValidation:
    def test_no_question_no_hypothesis_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest()
