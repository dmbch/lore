"""Instrumentation tests — spans, attributes, structured logs."""

from collections.abc import Sequence

from lore.domain import (
    ArchivistOutput,
    ConsultLoreRequest,
    Resolution,
)
from lore.repositories import AttestationRecord
from tests.orchestrator.conftest import (
    instrumented,
    make_hypothesis_result,
    make_orchestrator,
    write_request,
)


class TestConsultProducesStageSpans:
    """Orchestrator stages produce named spans with parent-child relationships."""

    async def test_read_path_produces_stage_spans(self) -> None:
        with instrumented() as (fixture, spans, _):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )

        span_names = {s.name for s in spans.get_finished_spans()}
        expected = {
            "lore.consult",
            "lore.interpret",
            "lore.search_candidates",
            "lore.enrich",
            "lore.reason",
        }
        assert expected <= span_names

        root = next(s for s in spans.get_finished_spans() if s.name == "lore.consult")
        assert root.context is not None
        root_span_id = root.context.span_id

        child_names = {"lore.interpret", "lore.search_candidates", "lore.enrich", "lore.reason"}
        for span in spans.get_finished_spans():
            if span.name in child_names:
                parent = span.parent
                assert parent is not None
                assert parent.span_id == root_span_id

    async def test_write_path_produces_record_spans(self) -> None:
        with instrumented(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Noted.",
                resolutions=[Resolution(contributes="novel claim")],
            ),
        ) as (fixture, spans, _):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=write_request(),
                correlation_id="corr-1",
            )

        span_names = {s.name for s in spans.get_finished_spans()}
        assert "lore.embed_novels" in span_names
        assert "lore.record" in span_names


class TestConsultSpanAttributes:
    """The root consult span carries a path attribute."""

    async def test_consult_span_carries_read_path_attribute(self) -> None:
        with instrumented() as (fixture, spans, _):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )

        root = next(s for s in spans.get_finished_spans() if s.name == "lore.consult")
        assert root.attributes is not None
        assert root.attributes.get("path") == "read"

    async def test_consult_span_carries_write_path_attribute(self) -> None:
        with instrumented(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Noted.",
                resolutions=[Resolution(contributes="novel claim")],
            ),
        ) as (fixture, spans, _):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=write_request(),
                correlation_id="corr-1",
            )

        root = next(s for s in spans.get_finished_spans() if s.name == "lore.consult")
        assert root.attributes is not None
        assert root.attributes.get("path") == "write"


class TestConsultEmitsStructuredLogs:
    """Structured log events are emitted at stage boundaries."""

    async def test_consult_emits_start_log_event(self) -> None:
        with instrumented() as (fixture, _, cap):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )

        start_events = [e for e in cap if e.get("event") == "consult.start"]
        assert len(start_events) == 1
        assert start_events[0]["path"] == "read"

    async def test_read_path_emits_stage_boundary_logs(self) -> None:
        with instrumented() as (fixture, _, cap):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )

        events = {e.get("event") for e in cap}
        assert "consult.interpreted" in events
        assert "consult.retrieved" in events
        assert "consult.reasoned" in events

    async def test_write_path_emits_recorder_log_events(self) -> None:
        with instrumented(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Noted.",
                resolutions=[Resolution(contributes="novel claim")],
            ),
        ) as (fixture, _, cap):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=write_request(),
                correlation_id="corr-1",
            )

        events = {e.get("event") for e in cap}
        assert "resolution.contribute" in events

    async def test_contribute_log_event_redacts_raw_novel_content(self) -> None:
        import hashlib

        content = "a sentinel proposition unique enough not to collide with field names"
        with instrumented(
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Noted.",
                resolutions=[Resolution(contributes=content)],
            ),
        ) as (fixture, _, cap):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=write_request(),
                correlation_id="corr-1",
            )

        contribute_events = [e for e in cap if e.get("event") == "resolution.contribute"]
        assert len(contribute_events) == 1
        event = contribute_events[0]
        digest = hashlib.sha256(content.encode()).hexdigest()[:16]

        assert content not in str(event)
        assert event["contributes_length"] == len(content)
        assert event["contributes_sha256"] == digest

    async def test_reason_result_log_event_carries_reasoning_at_debug(self) -> None:
        """DEBUG logs are for actual debugging; the Archivist's reasoning belongs there.

        Operators opt in to DEBUG when they need to triage; the chain of
        thought is the highest-value signal at that moment.
        """
        sentinel = "sentinel reasoning text that should appear in debug logs"
        with instrumented(
            archivist_output=ArchivistOutput(
                reasoning=sentinel,
                answer="Noted.",
                resolutions=[],
            ),
        ) as (fixture, _, cap):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )

        debug_events = [
            e
            for e in cap
            if e.get("event") == "consult.reason.result" and e.get("log_level") == "debug"
        ]
        assert len(debug_events) == 1
        assert debug_events[0]["reasoning"] == sentinel

    async def test_identity_match_emits_recorder_log_event(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        with instrumented(
            search_results=[make_hypothesis_result(id=hypothesis_id)],
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Agreed.",
                resolutions=[Resolution(corroborates=hypothesis_id)],
            ),
        ) as (fixture, _, cap):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=write_request(),
                correlation_id="corr-1",
            )

        events = {e.get("event") for e in cap}
        assert "resolution.paraphrase" in events

    async def test_archivist_notes_emit_count_at_info_not_content(self) -> None:
        """INFO carries only the count; content is client-controllable LLM text."""
        sentinel = "ambiguous proposition X"
        with instrumented(
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                notes=[sentinel],
            ),
        ) as (fixture, _, cap):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )
        info_events = [
            e for e in cap if e.get("event") == "consult.notes" and e.get("log_level") == "info"
        ]
        assert len(info_events) == 1
        event = info_events[0]
        assert event["count"] == 1
        # INFO level events deliberately do not carry the note content.
        assert sentinel not in str(event)

    async def test_archivist_notes_emit_content_at_debug(self) -> None:
        """DEBUG carries the note content for operators triaging ambiguous propositions."""
        sentinel = "ambiguous proposition X"
        with instrumented(
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                notes=[sentinel],
            ),
        ) as (fixture, _, cap):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )
        debug_events = [
            e
            for e in cap
            if e.get("event") == "consult.note_contents" and e.get("log_level") == "debug"
        ]
        assert len(debug_events) == 1
        assert sentinel in debug_events[0]["notes"]

    async def test_empty_archivist_notes_do_not_emit_consult_notes_log_event(self) -> None:
        with instrumented() as (fixture, _, cap):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )
        notes_events = [e for e in cap if e.get("event") == "consult.notes"]
        assert notes_events == []


class TestWritePathOneFetchOverUnion:
    """Orchestrator issues one full-list fetch over corroborated+contradicted IDs."""

    async def test_attestation_fetch_keys_equal_union(self) -> None:
        cor_id = "550e8400-e29b-41d4-a716-446655440000"
        con_a = "660e8400-e29b-41d4-a716-446655440000"
        con_b = "770e8400-e29b-41d4-a716-446655440000"

        fixture = make_orchestrator(
            search_results=[
                make_hypothesis_result(id=cor_id),
                make_hypothesis_result(id=con_a),
                make_hypothesis_result(id=con_b),
            ],
            by_hypotheses={cor_id: [], con_a: [], con_b: []},
            archivist_output=ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[
                    Resolution(corroborates=cor_id, contradicts=[con_a]),
                    Resolution(contributes="novel", contradicts=[con_b]),
                ],
            ),
        )
        # Capture find_by_hypotheses calls inside the write transaction.
        original = fixture.attestations.find_by_hypotheses
        calls: list[set[str]] = []

        async def tracking_find(hids: Sequence[str]) -> dict[str, list[AttestationRecord]]:
            calls.append(set(hids))
            return await original(hids)

        fixture.attestations.find_by_hypotheses = tracking_find  # pyright: ignore[reportAttributeAccessIssue]

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.7),
            correlation_id="corr-1",
        )

        # Two find_by_hypotheses calls: one in enrich (read-path), one in record.
        # The write-path call (the second) must cover the union.
        assert len(calls) == 2
        assert calls[1] == {cor_id, con_a, con_b}
