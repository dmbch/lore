"""Tests for domain types: cross-layer boundary validation."""

from datetime import date

import pytest
from pydantic import ValidationError

from lore.domain.types import (
    ArchivistInput,
    ArchivistOutput,
    ConsultLoreRequest,
    ConsultLoreResponse,
    InterpreterInput,
    InterpreterOutput,
    Resolution,
    SearchResult,
    TrustSignal,
    WriteContext,
)


def _valid_snapshot(**overrides: object) -> TrustSignal:
    defaults: dict[str, object] = {
        "c_oracle_raw": 0.5,
        "timestamp": 1000,
        "c_herd_prior": 0.3,
        "c_herd_now": 0.6,
        "n_oracle_prior": 0,
    }
    defaults.update(overrides)
    return TrustSignal(**defaults)  # pyright: ignore[reportArgumentType]


class TestTrustSignalValidation:
    """TrustSignal validates confidence ranges and timestamp."""

    def test_accepts_valid_data(self) -> None:
        s = _valid_snapshot()
        assert s.c_oracle_raw == 0.5
        assert s.timestamp == 1000
        assert s.c_herd_prior == 0.3
        assert s.c_herd_now == 0.6

    def test_accepts_boundary_confidence_values(self) -> None:
        s = _valid_snapshot(c_oracle_raw=-1.0, c_herd_prior=1.0, c_herd_now=-1.0)
        assert s.c_oracle_raw == -1.0
        assert s.c_herd_prior == 1.0
        assert s.c_herd_now == -1.0

    def test_rejects_c_oracle_raw_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="c_oracle_raw"):
            _valid_snapshot(c_oracle_raw=1.5)

    def test_rejects_c_herd_prior_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="c_herd_prior"):
            _valid_snapshot(c_herd_prior=-1.1)

    def test_rejects_c_herd_now_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="c_herd_now"):
            _valid_snapshot(c_herd_now=float("inf"))

    def test_rejects_nan_confidence(self) -> None:
        with pytest.raises(ValueError, match="c_oracle_raw"):
            _valid_snapshot(c_oracle_raw=float("nan"))

    def test_rejects_negative_timestamp(self) -> None:
        with pytest.raises(ValueError, match="timestamp"):
            _valid_snapshot(timestamp=-1)

    def test_accepts_zero_timestamp(self) -> None:
        s = _valid_snapshot(timestamp=0)
        assert s.timestamp == 0


class TestTrustSignalImmutability:
    """Frozen Pydantic model: mutation raises ValidationError."""

    def test_is_frozen(self) -> None:
        s = _valid_snapshot()
        with pytest.raises(ValidationError, match="frozen"):
            s.c_oracle_raw = 0.9  # pyright: ignore[reportAttributeAccessIssue]


class TestTrustSignalModelConstruct:
    """model_construct() skips validation: hot path for DB reads."""

    def test_model_construct_skips_validation(self) -> None:
        s = TrustSignal.model_construct(
            c_oracle_raw=999.0, timestamp=-1, c_herd_prior=0.0, c_herd_now=0.0
        )
        assert s.c_oracle_raw == 999.0
        assert s.timestamp == -1


# --- MCP boundary types ---


class TestConsultLoreRequest:
    """ConsultLoreRequest: tightened validator truth table.

    A call must carry a ``question``, a ``hypothesis``, or both; if a
    hypothesis is present, ``confidence`` is mandatory. ``context`` and
    ``reasoning`` decorate a valid call but cannot stand alone.
    """

    # --- Accepted shapes ---

    def test_consult_request_only_question_accepted(self) -> None:
        r = ConsultLoreRequest(question="what?")
        assert r.question == "what?"
        assert r.hypothesis is None
        assert r.context is None
        assert r.reasoning is None
        assert r.confidence is None

    def test_consult_request_question_with_context_accepted(self) -> None:
        r = ConsultLoreRequest(question="what?", context="investigating outage")
        assert r.question == "what?"
        assert r.context == "investigating outage"

    def test_consult_request_question_with_reasoning_accepted(self) -> None:
        r = ConsultLoreRequest(question="what?", reasoning="logs show errors")
        assert r.question == "what?"
        assert r.reasoning == "logs show errors"

    def test_consult_request_hypothesis_with_positive_confidence_accepted(self) -> None:
        r = ConsultLoreRequest(hypothesis="claim", confidence=0.7)
        assert r.hypothesis == "claim"
        assert r.confidence == 0.7

    def test_consult_request_hypothesis_with_negative_confidence_accepted(self) -> None:
        r = ConsultLoreRequest(hypothesis="claim", confidence=-0.4)
        assert r.hypothesis == "claim"
        assert r.confidence == -0.4

    def test_consult_request_hypothesis_with_zero_confidence_accepted(self) -> None:
        # Vacuous write: 0.0 is a scalar, not absence of a scalar.
        r = ConsultLoreRequest(hypothesis="claim", confidence=0.0)
        assert r.hypothesis == "claim"
        assert r.confidence == 0.0

    def test_consult_request_question_and_hypothesis_with_confidence_accepted(self) -> None:
        r = ConsultLoreRequest(
            question="What happened?",
            context="Investigating outage",
            hypothesis="Service X caused the outage",
            reasoning="Logs show errors in Service X",
            confidence=0.7,
        )
        assert r.question == "What happened?"
        assert r.context == "Investigating outage"
        assert r.hypothesis == "Service X caused the outage"
        assert r.reasoning == "Logs show errors in Service X"
        assert r.confidence == 0.7

    # --- Rejected shapes ---

    def test_consult_request_all_fields_none_raises(self) -> None:
        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest()

    def test_consult_request_only_context_raises(self) -> None:
        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest(context="just context")

    def test_consult_request_only_reasoning_raises(self) -> None:
        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest(reasoning="just reasoning")

    def test_consult_request_context_and_reasoning_without_question_or_hypothesis_raises(
        self,
    ) -> None:
        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest(context="ctx", reasoning="rsn")

    def test_consult_request_only_hypothesis_raises(self) -> None:
        with pytest.raises(ValidationError, match="requires a confidence scalar"):
            ConsultLoreRequest(hypothesis="claim")

    def test_consult_request_hypothesis_with_confidence_none_raises(self) -> None:
        with pytest.raises(ValidationError, match="requires a confidence scalar"):
            ConsultLoreRequest(hypothesis="claim", confidence=None)

    def test_consult_request_only_confidence_raises(self) -> None:
        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest(confidence=0.5)

    def test_consult_request_confidence_with_question_but_no_hypothesis_raises(self) -> None:
        with pytest.raises(ValidationError, match="requires a hypothesis"):
            ConsultLoreRequest(question="q", confidence=0.5)

    # --- Blank strings normalize to None ---

    def test_consult_request_blank_hypothesis_with_confidence_rejected(self) -> None:
        # Whitespace-only hypothesis folds to None, so the gate sees no claim.
        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest(hypothesis="   ", confidence=0.5)

    def test_consult_request_empty_hypothesis_with_confidence_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest(hypothesis="", confidence=0.5)

    def test_consult_request_blank_question_alone_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest(question="   ")

    def test_consult_request_empty_question_alone_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires a question, a hypothesis, or both"):
            ConsultLoreRequest(question="")

    def test_consult_request_blank_hypothesis_with_question_and_confidence_rejected(self) -> None:
        # A real question does not rescue a blank hypothesis: confidence still
        # has no claim to land on.
        with pytest.raises(ValidationError, match="requires a hypothesis"):
            ConsultLoreRequest(question="what?", hypothesis="   ", confidence=0.5)

    def test_consult_request_blank_context_normalizes_to_none(self) -> None:
        r = ConsultLoreRequest(question="what?", context="   ")
        assert r.context is None

    def test_consult_request_hypothesis_surrounding_whitespace_preserved(self) -> None:
        # A real claim passes verbatim: only wholly-blank strings become None.
        r = ConsultLoreRequest(hypothesis="  Service X caused it  ", confidence=0.5)
        assert r.hypothesis == "  Service X caused it  "

    # --- Unchanged invariants ---

    def test_is_frozen(self) -> None:
        r = ConsultLoreRequest(question="q")
        with pytest.raises(ValidationError, match="frozen"):
            r.question = "changed"  # pyright: ignore[reportAttributeAccessIssue]

    def test_confidence_at_negative_one_accepted(self) -> None:
        r = ConsultLoreRequest(question="q", hypothesis="h", confidence=-1.0)
        assert r.confidence == -1.0

    def test_confidence_at_positive_one_accepted(self) -> None:
        r = ConsultLoreRequest(question="q", hypothesis="h", confidence=1.0)
        assert r.confidence == 1.0

    def test_confidence_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            ConsultLoreRequest(question="q", hypothesis="h", confidence=1.1)

    def test_confidence_below_negative_one_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            ConsultLoreRequest(question="q", hypothesis="h", confidence=-1.1)


class TestConsultLoreResponse:
    """ConsultLoreResponse: requires an answer string."""

    def test_requires_answer(self) -> None:
        with pytest.raises(ValidationError):
            ConsultLoreResponse()  # pyright: ignore[reportCallIssue] - omitting the required field is the behavior under test

    def test_is_frozen(self) -> None:
        r = ConsultLoreResponse(answer="text")
        with pytest.raises(ValidationError, match="frozen"):
            r.answer = "changed"  # pyright: ignore[reportAttributeAccessIssue]

    def test_accepts_answer(self) -> None:
        r = ConsultLoreResponse(answer="The herd believes X.")
        assert r.answer == "The herd believes X."


# --- Interpreter types ---


class TestInterpreterInput:
    """InterpreterInput: required consult date, optional passthrough from MCP request."""

    def test_construct_without_today_raises(self) -> None:
        with pytest.raises(ValidationError, match="today"):
            InterpreterInput()  # pyright: ignore[reportCallIssue] - omitting the required field is the behavior under test

    def test_construct_with_only_today_defaults_passthrough_to_none(self) -> None:
        i = InterpreterInput(today=date(2026, 7, 3))
        assert i.question is None
        assert i.hypothesis is None
        assert i.context is None
        assert i.reasoning is None

    def test_is_frozen(self) -> None:
        i = InterpreterInput(today=date(2026, 7, 3))
        with pytest.raises(ValidationError, match="frozen"):
            i.question = "changed"  # pyright: ignore[reportAttributeAccessIssue]


class TestInterpreterOutput:
    """InterpreterOutput: normalized question, decomposed propositions, keywords."""

    def test_defaults_to_empty_lists(self) -> None:
        o = InterpreterOutput()
        assert o.question is None
        assert o.propositions == []
        assert o.keywords == []

    def test_is_frozen(self) -> None:
        o = InterpreterOutput()
        with pytest.raises(ValidationError, match="frozen"):
            o.question = "changed"  # pyright: ignore[reportAttributeAccessIssue]

    def test_accepts_populated_fields(self) -> None:
        o = InterpreterOutput(
            question="normalized question",
            propositions=["Service X switched to gRPC"],
            keywords=["gRPC", "Service X"],
        )
        assert o.question == "normalized question"
        assert o.propositions == ["Service X switched to gRPC"]
        assert o.keywords == ["gRPC", "Service X"]

    def test_propositions_bounded_at_sixteen(self) -> None:
        with pytest.raises(ValidationError, match="at most 16"):
            InterpreterOutput(propositions=[f"p{i}" for i in range(17)])


# --- Search result types ---


def _valid_search_result(**overrides: object) -> SearchResult:
    defaults: dict[str, object] = {
        "id": "hyp-001",
        "content": "Service X switched to gRPC in Q3",
        "c_herd": 0.4,
        "attestation_count": 3,
        "last_attested": 1700000000,
        "score": 0.7,
        "proximity": 0.8,
    }
    defaults.update(overrides)
    return SearchResult(**defaults)  # pyright: ignore[reportArgumentType]


class TestSearchResult:
    """SearchResult: retrieval candidate with scores and epistemic snapshot."""

    def test_accepts_valid_data(self) -> None:
        s = _valid_search_result()
        assert s.id == "hyp-001"
        assert s.content == "Service X switched to gRPC in Q3"
        assert s.c_herd == 0.4
        assert s.attestation_count == 3
        assert s.last_attested == 1700000000
        assert s.score == 0.7
        assert s.proximity == 0.8

    def test_is_frozen(self) -> None:
        s = _valid_search_result()
        with pytest.raises(ValidationError, match="frozen"):
            s.c_herd = 0.9  # pyright: ignore[reportAttributeAccessIssue]

    def test_c_herd_validated_in_range(self) -> None:
        with pytest.raises(ValueError, match="c_herd"):
            _valid_search_result(c_herd=1.5)

    def test_score_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="score"):
            _valid_search_result(score=-0.1)

    def test_score_rejects_above_one(self) -> None:
        with pytest.raises(ValueError, match="score"):
            _valid_search_result(score=1.1)

    def test_proximity_rejects_outside_cosine_range(self) -> None:
        with pytest.raises(ValueError, match="proximity"):
            _valid_search_result(proximity=-1.1)
        with pytest.raises(ValueError, match="proximity"):
            _valid_search_result(proximity=1.1)

    def test_proximity_accepts_full_cosine_range(self) -> None:
        # Cosine similarity is in [-1, 1]; orthogonal authority-only rows
        # default to 0.0, antiparallel claims reach -1.0.
        assert _valid_search_result(proximity=-1.0).proximity == -1.0
        assert _valid_search_result(proximity=0.0).proximity == 0.0
        assert _valid_search_result(proximity=1.0).proximity == 1.0

    def test_proximity_defaults_to_zero(self) -> None:
        s = SearchResult.model_validate(
            {
                "id": "hyp-001",
                "content": "x",
                "c_herd": 0.0,
                "attestation_count": 0,
                "last_attested": 0,
                "score": 0.5,
            }
        )
        assert s.proximity == 0.0

    def test_attestation_count_non_negative(self) -> None:
        with pytest.raises(ValueError, match="attestation_count"):
            _valid_search_result(attestation_count=-1)

    def test_last_attested_non_negative(self) -> None:
        with pytest.raises(ValueError, match="last_attested"):
            _valid_search_result(last_attested=-1)


# --- Archivist types ---


class TestArchivistInput:
    """ArchivistInput: unified input for both read and write paths."""

    def test_archivist_input_accepts_all_fields(self) -> None:
        r = _valid_search_result()
        a = ArchivistInput(
            question="What happened?",
            hypothesis="Service X caused the outage",
            context="Investigating outage",
            reasoning="Logs show errors in Service X",
            propositions=["Service X failed", "Outage started at 3am"],
            retrieved=[r],
            today=date(2026, 7, 3),
        )
        assert a.question == "What happened?"
        assert a.hypothesis == "Service X caused the outage"
        assert a.context == "Investigating outage"
        assert a.reasoning == "Logs show errors in Service X"
        assert a.propositions == ["Service X failed", "Outage started at 3am"]
        assert a.retrieved == [r]
        assert a.today == date(2026, 7, 3)

    def test_archivist_input_construct_without_today_raises(self) -> None:
        with pytest.raises(ValidationError, match="today"):
            ArchivistInput(retrieved=[])  # pyright: ignore[reportCallIssue] - omitting the required field is the behavior under test

    def test_archivist_input_only_retrieved_and_today_required(self) -> None:
        a = ArchivistInput(retrieved=[], today=date(2026, 7, 3))
        assert a.question is None
        assert a.hypothesis is None
        assert a.context is None
        assert a.reasoning is None
        assert a.propositions == []
        assert a.retrieved == []

    def test_archivist_input_is_frozen(self) -> None:
        a = ArchivistInput(retrieved=[], today=date(2026, 7, 3))
        with pytest.raises(ValidationError, match="frozen"):
            a.question = "changed"  # pyright: ignore[reportAttributeAccessIssue]


class TestResolution:
    """Resolution: paraphrase / orthogonal-novel, optionally with contradicts."""

    # --- Paraphrase: corroborates set, contributes empty ---

    def test_paraphrase_valid(self) -> None:
        r = Resolution(corroborates="abc")
        assert r.corroborates == "abc"
        assert r.contributes is None
        assert r.contradicts == []

    def test_paraphrase_with_contradicts(self) -> None:
        r = Resolution(corroborates="abc", contradicts=["x", "y"])
        assert r.corroborates == "abc"
        assert r.contradicts == ["x", "y"]

    # --- Orthogonal-novel: contributes set, corroborates empty ---

    def test_contributes_valid(self) -> None:
        r = Resolution(contributes="new claim")
        assert r.contributes == "new claim"
        assert r.corroborates is None
        assert r.contradicts == []

    def test_contributes_with_contradicts(self) -> None:
        r = Resolution(contributes="new claim", contradicts=["h1"])
        assert r.contributes == "new claim"
        assert r.contradicts == ["h1"]

    # --- Mutual exclusion: exactly one of corroborates or contributes ---

    def test_mutual_exclusion_both_set(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            Resolution(corroborates="abc", contributes="new claim")

    def test_mutual_exclusion_neither_set(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            Resolution()

    # --- Per-resolution disjointness ---

    def test_corroborates_cannot_appear_in_contradicts(self) -> None:
        with pytest.raises(ValidationError, match="contradicts"):
            Resolution(corroborates="abc", contradicts=["abc", "x"])

    def test_contradicts_must_be_unique(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            Resolution(contributes="new claim", contradicts=["h1", "h1"])

    # --- ID and content validation ---

    def test_corroborates_must_be_non_empty(self) -> None:
        with pytest.raises(ValidationError, match="corroborates"):
            Resolution(corroborates="")

    def test_contributes_must_be_non_empty(self) -> None:
        with pytest.raises(ValidationError, match="contributes"):
            Resolution(contributes="")

    def test_contradicts_ids_must_be_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            Resolution(contributes="claim", contradicts=[""])

    # --- Frozen ---

    def test_is_frozen(self) -> None:
        r = Resolution(corroborates="abc")
        with pytest.raises(ValidationError, match="frozen"):
            r.corroborates = "changed"  # pyright: ignore[reportAttributeAccessIssue]


class TestArchivistOutput:
    """ArchivistOutput: unified output for both read and write paths."""

    def test_archivist_output_requires_answer(self) -> None:
        with pytest.raises(ValidationError):
            ArchivistOutput()  # pyright: ignore[reportCallIssue] - omitting the required field is the behavior under test

    def test_archivist_output_defaults_to_empty_resolutions(self) -> None:
        o = ArchivistOutput(reasoning="test reasoning", answer="The evidence supports X.")
        assert o.resolutions == []
        assert o.notes == []
        assert o.answer == "The evidence supports X."

    def test_archivist_output_is_frozen(self) -> None:
        o = ArchivistOutput(reasoning="test reasoning", answer="a")
        with pytest.raises(ValidationError, match="frozen"):
            o.answer = "changed"  # pyright: ignore[reportAttributeAccessIssue]

    # --- notes channel ---

    def test_archivist_output_notes_default_empty(self) -> None:
        o = ArchivistOutput(reasoning="r", answer="a")
        assert o.notes == []

    def test_archivist_output_notes_round_trip(self) -> None:
        o = ArchivistOutput(
            reasoning="r",
            answer="a",
            notes=["ambiguous proposition X", "competing hypothesis Y was close"],
        )
        assert o.notes == ["ambiguous proposition X", "competing hypothesis Y was close"]

    def test_archivist_output_two_constructs_have_independent_resolutions_lists(self) -> None:
        """default_factory=list gives each instance its own list, not a shared mutable default."""
        a = ArchivistOutput.model_construct(reasoning="r", answer="a")
        b = ArchivistOutput.model_construct(reasoning="r", answer="a")
        assert a.resolutions is not b.resolutions

    # --- Cross-resolution disjointness ---

    def test_disjoint_resolutions_accepted(self) -> None:
        o = ArchivistOutput(
            reasoning="r",
            answer="a",
            resolutions=[
                Resolution(corroborates="aaa", contradicts=["bbb"]),
                Resolution(contributes="new claim", contradicts=["ccc"]),
            ],
        )
        assert len(o.resolutions) == 2

    def test_duplicate_corroborates_across_resolutions_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[
                    Resolution(corroborates="aaa"),
                    Resolution(corroborates="aaa"),
                ],
            )

    def test_duplicate_contradicts_across_resolutions_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[
                    Resolution(contributes="A", contradicts=["xxx"]),
                    Resolution(contributes="B", contradicts=["xxx"]),
                ],
            )

    def test_corroborates_colliding_with_contradicts_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[
                    Resolution(corroborates="xxx"),
                    Resolution(contributes="B", contradicts=["xxx"]),
                ],
            )

    def test_contributes_resolutions_with_distinct_contradicts_accepted(self) -> None:
        o = ArchivistOutput(
            reasoning="r",
            answer="a",
            resolutions=[
                Resolution(contributes="claim A", contradicts=["xxx"]),
                Resolution(contributes="claim B", contradicts=["yyy"]),
            ],
        )
        assert len(o.resolutions) == 2


class TestArchivistOutputDisjointNovels:
    """ArchivistOutput rejects duplicate `contributes` strings across resolutions.

    Mirrors `_disjoint_resolution_ids` but on the novel-content slot. The Archivist's
    one-resolution-per-proposition rule is a maximum, not a minimum: if two
    propositions would produce the same novel statement, they must collapse into a
    single resolution.
    """

    def test_archivist_output_rejects_duplicate_contributes_across_resolutions(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            ArchivistOutput(
                reasoning="r",
                answer="a",
                resolutions=[
                    Resolution(contributes="X"),
                    Resolution(contributes="X"),
                ],
            )

    def test_archivist_output_accepts_distinct_contributes_across_resolutions(self) -> None:
        o = ArchivistOutput(
            reasoning="r",
            answer="a",
            resolutions=[
                Resolution(contributes="claim A"),
                Resolution(contributes="claim B"),
            ],
        )
        assert len(o.resolutions) == 2

    def test_archivist_output_corroborates_id_and_contributes_string_do_not_collide(self) -> None:
        # corroborates carries a UUID; contributes carries content. Even when the
        # literal strings match, the validator only checks within the contributes-only
        # slot, not across slots.
        o = ArchivistOutput(
            reasoning="r",
            answer="a",
            resolutions=[
                Resolution(corroborates="abc-uuid"),
                Resolution(contributes="abc-uuid"),
            ],
        )
        assert len(o.resolutions) == 2


# --- Record stage / write path ---


def _valid_context(**overrides: object) -> WriteContext:
    defaults: dict[str, object] = {
        "oracle_id": "oracle-1",
        "correlation_id": "corr-1",
        "confidence": 0.7,
        "t_now": 2_000_000_000,
    }
    defaults.update(overrides)
    return WriteContext(**defaults)  # pyright: ignore[reportArgumentType]


class TestWriteContextValidation:
    """WriteContext bundles the four write-path coordinates with boundary checks."""

    def test_accepts_valid_data(self) -> None:
        c = _valid_context()
        assert c.oracle_id == "oracle-1"
        assert c.correlation_id == "corr-1"
        assert c.confidence == 0.7
        assert c.t_now == 2_000_000_000

    def test_accepts_boundary_confidence_values(self) -> None:
        assert _valid_context(confidence=-1.0).confidence == -1.0
        assert _valid_context(confidence=1.0).confidence == 1.0

    def test_accepts_zero_t_now(self) -> None:
        assert _valid_context(t_now=0).t_now == 0

    def test_rejects_empty_oracle_id(self) -> None:
        with pytest.raises(ValueError, match="oracle_id"):
            _valid_context(oracle_id="")

    def test_rejects_empty_correlation_id(self) -> None:
        with pytest.raises(ValueError, match="correlation_id"):
            _valid_context(correlation_id="")

    def test_rejects_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            _valid_context(confidence=1.5)

    def test_rejects_nan_confidence(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            _valid_context(confidence=float("nan"))

    def test_rejects_negative_t_now(self) -> None:
        with pytest.raises(ValueError, match="t_now"):
            _valid_context(t_now=-1)


class TestWriteContextImmutability:
    """Frozen Pydantic model: mutation raises ValidationError."""

    def test_is_frozen(self) -> None:
        c = _valid_context()
        with pytest.raises(ValidationError, match="frozen"):
            c.oracle_id = "other"  # pyright: ignore[reportAttributeAccessIssue]
