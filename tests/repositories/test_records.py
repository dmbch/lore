"""Tests for repository record types.

Record types are Pydantic models (frozen) that mirror the database schema.
They validate on construction, mirroring DB constraints so corrupt data
fails loudly at record construction, not downstream.
"""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from lore.repositories.records import (
    AttestationRecord,
    CacheEntry,
    HypothesisRecord,
    HypothesisResult,
    RequestRecord,
    build_attestation_records,
)


def _valid_attestation(**overrides: object) -> AttestationRecord:
    defaults: dict[str, object] = {
        "id": "00000000-0000-0000-0000-000000000001",
        "hypothesis_id": "00000000-0000-0000-0000-000000000002",
        "oracle_id": "oracle-1",
        "correlation_id": "00000000-0000-0000-0000-000000000003",
        "timestamp": 1000,
        "t_oracle": 0.5,
        "c_oracle_raw": 0.5,
        "c_oracle_discounted": 0.25,
        "c_herd": 0.3,
        "n_oracle_prior": 0,
    }
    defaults.update(overrides)
    # Builder pattern: values are runtime-correct per field, but the dict types as
    # dict[str, object]. The alternative (model_copy on a base instance) requires
    # constructing a valid record first, defeating the builder's purpose of testing
    # invalid constructions. The pyright ignore below covers the **kwargs splat.
    return AttestationRecord(**defaults)  # pyright: ignore[reportArgumentType]


def _valid_hypothesis(**overrides: object) -> HypothesisRecord:
    defaults: dict[str, object] = {
        "id": "00000000-0000-0000-0000-000000000001",
        "content": "Service X uses gRPC",
        "created_at": 1000,
    }
    defaults.update(overrides)
    return HypothesisRecord(**defaults)  # pyright: ignore[reportArgumentType]  # same rationale as above


class TestAttestationRecordValidation:
    def test_attestation_record_accepts_valid_data(self) -> None:
        r = _valid_attestation()
        assert r.id == "00000000-0000-0000-0000-000000000001"
        assert r.timestamp == 1000
        assert r.t_oracle == 0.5
        assert r.c_oracle_discounted == 0.25

    def test_attestation_record_rejects_non_uuid_id(self) -> None:
        with pytest.raises(ValueError, match="id"):
            _valid_attestation(id="not-a-uuid")

    def test_attestation_record_rejects_empty_id(self) -> None:
        with pytest.raises(ValueError, match="id"):
            _valid_attestation(id="")

    def test_attestation_record_rejects_non_uuid_hypothesis_id(self) -> None:
        with pytest.raises(ValueError, match="hypothesis_id"):
            _valid_attestation(hypothesis_id="bad")

    def test_attestation_record_rejects_empty_hypothesis_id(self) -> None:
        with pytest.raises(ValueError, match="hypothesis_id"):
            _valid_attestation(hypothesis_id="")

    def test_attestation_record_rejects_empty_oracle_id(self) -> None:
        with pytest.raises(ValueError, match="oracle_id"):
            _valid_attestation(oracle_id="")

    def test_attestation_record_accepts_non_uuid_correlation_id(self) -> None:
        r = _valid_attestation(correlation_id="trace-abc-123")
        assert r.correlation_id == "trace-abc-123"

    def test_attestation_record_rejects_empty_correlation_id(self) -> None:
        with pytest.raises(ValueError, match="correlation_id"):
            _valid_attestation(correlation_id="")

    def test_attestation_record_rejects_negative_timestamp(self) -> None:
        with pytest.raises(ValueError, match="timestamp"):
            _valid_attestation(timestamp=-1)

    def test_attestation_record_accepts_zero_timestamp(self) -> None:
        r = _valid_attestation(timestamp=0)
        assert r.timestamp == 0

    # --- t_oracle: unit interval [0, 1] ---

    def test_attestation_record_rejects_t_oracle_below_zero(self) -> None:
        with pytest.raises(ValueError, match="t_oracle"):
            _valid_attestation(t_oracle=-0.01)

    def test_attestation_record_rejects_t_oracle_above_one(self) -> None:
        with pytest.raises(ValueError, match="t_oracle"):
            _valid_attestation(t_oracle=1.01)

    def test_attestation_record_rejects_t_oracle_nan(self) -> None:
        with pytest.raises(ValueError, match="t_oracle"):
            _valid_attestation(t_oracle=float("nan"))

    def test_attestation_record_rejects_t_oracle_inf(self) -> None:
        with pytest.raises(ValueError, match="t_oracle"):
            _valid_attestation(t_oracle=float("inf"))

    def test_attestation_record_accepts_t_oracle_at_boundaries(self) -> None:
        r_low = _valid_attestation(t_oracle=0.0)
        r_high = _valid_attestation(t_oracle=1.0)
        assert r_low.t_oracle == 0.0
        assert r_high.t_oracle == 1.0

    # --- c_oracle_raw: confidence [-1, 1] ---

    def test_attestation_record_rejects_c_oracle_raw_below_minus_one(self) -> None:
        with pytest.raises(ValueError, match="c_oracle_raw"):
            _valid_attestation(c_oracle_raw=-1.01)

    def test_attestation_record_rejects_c_oracle_raw_above_one(self) -> None:
        with pytest.raises(ValueError, match="c_oracle_raw"):
            _valid_attestation(c_oracle_raw=1.01)

    def test_attestation_record_rejects_c_oracle_raw_nan(self) -> None:
        with pytest.raises(ValueError, match="c_oracle_raw"):
            _valid_attestation(c_oracle_raw=float("nan"))

    # --- c_oracle_discounted: confidence [-1, 1] ---

    def test_attestation_record_rejects_c_oracle_discounted_below_minus_one(self) -> None:
        with pytest.raises(ValueError, match="c_oracle_discounted"):
            _valid_attestation(c_oracle_discounted=-1.01)

    def test_attestation_record_rejects_c_oracle_discounted_above_one(self) -> None:
        with pytest.raises(ValueError, match="c_oracle_discounted"):
            _valid_attestation(c_oracle_discounted=1.01)

    def test_attestation_record_rejects_c_oracle_discounted_nan(self) -> None:
        with pytest.raises(ValueError, match="c_oracle_discounted"):
            _valid_attestation(c_oracle_discounted=float("nan"))

    def test_attestation_record_rejects_c_oracle_discounted_inf(self) -> None:
        with pytest.raises(ValueError, match="c_oracle_discounted"):
            _valid_attestation(c_oracle_discounted=float("inf"))

    def test_attestation_record_accepts_c_oracle_discounted_at_boundaries(self) -> None:
        r_low = _valid_attestation(c_oracle_discounted=-1.0)
        r_high = _valid_attestation(c_oracle_discounted=1.0)
        assert r_low.c_oracle_discounted == -1.0
        assert r_high.c_oracle_discounted == 1.0

    # --- c_herd: confidence [-1, 1] ---

    def test_attestation_record_rejects_c_herd_outside_range(self) -> None:
        with pytest.raises(ValueError, match="c_herd"):
            _valid_attestation(c_herd=1.5)

    def test_attestation_record_rejects_c_herd_inf(self) -> None:
        with pytest.raises(ValueError, match="c_herd"):
            _valid_attestation(c_herd=float("inf"))

    # --- storage bounds: all confidence fields at extremes ---

    def test_attestation_record_accepts_confidence_at_storage_bounds(self) -> None:
        r = _valid_attestation(c_oracle_raw=-1.0, c_oracle_discounted=-0.5, c_herd=1.0)
        assert r.c_oracle_raw == -1.0
        assert r.c_oracle_discounted == -0.5
        assert r.c_herd == 1.0

    # --- n_oracle_prior: non-negative count ---

    def test_attestation_record_rejects_negative_n_oracle_prior(self) -> None:
        with pytest.raises(ValueError, match="n_oracle_prior"):
            _valid_attestation(n_oracle_prior=-1)

    def test_attestation_record_accepts_zero_n_oracle_prior(self) -> None:
        r = _valid_attestation(n_oracle_prior=0)
        assert r.n_oracle_prior == 0


class TestHypothesisRecordValidation:
    def test_hypothesis_record_accepts_valid_data(self) -> None:
        r = _valid_hypothesis()
        assert r.id == "00000000-0000-0000-0000-000000000001"
        assert r.content == "Service X uses gRPC"

    def test_hypothesis_record_rejects_non_uuid_id(self) -> None:
        with pytest.raises(ValueError, match="id"):
            _valid_hypothesis(id="not-a-uuid")

    def test_hypothesis_record_rejects_empty_id(self) -> None:
        with pytest.raises(ValueError, match="id"):
            _valid_hypothesis(id="")

    def test_hypothesis_record_rejects_empty_content(self) -> None:
        with pytest.raises(ValueError, match="content"):
            _valid_hypothesis(content="")

    def test_hypothesis_record_rejects_negative_timestamp(self) -> None:
        with pytest.raises(ValueError, match="created_at"):
            _valid_hypothesis(created_at=-1)

    def test_hypothesis_record_accepts_zero_timestamp(self) -> None:
        r = _valid_hypothesis(created_at=0)
        assert r.created_at == 0


class TestRecordImmutability:
    def test_hypothesis_record_is_frozen(self) -> None:
        r = _valid_hypothesis()
        with pytest.raises(ValidationError, match="frozen"):
            r.content = "mutated"  # pyright: ignore[reportAttributeAccessIssue]

    def test_attestation_record_is_frozen(self) -> None:
        r = _valid_attestation()
        with pytest.raises(ValidationError, match="frozen"):
            r.c_oracle_raw = 0.99  # pyright: ignore[reportAttributeAccessIssue]


class TestModelConstruct:
    """model_construct() creates records without validation, hot path for DB reads."""

    def test_build_attestation_records_skips_validation(self) -> None:
        # Arrange: row with t_oracle outside [0, 1], fails validation
        row: dict[str, Any] = {
            "id": "00000000-0000-0000-0000-000000000001",
            "hypothesis_id": "00000000-0000-0000-0000-000000000002",
            "oracle_id": "oracle-1",
            "correlation_id": "corr-1",
            "timestamp": 1000,
            "t_oracle": -999.0,
            "c_oracle_raw": 0.5,
            "c_oracle_discounted": 0.25,
            "c_herd": 0.3,
            "n_oracle_prior": 0,
        }

        # Act: build_attestation_records should use model_construct (no validation)
        records = build_attestation_records(rows=[row])

        # Assert: record created with the invalid value intact
        assert len(records) == 1
        assert records[0].t_oracle == -999.0

    def test_build_attestation_records_coerces_uuid_objects_to_str(self) -> None:
        # Postgres returns ``uuid.UUID`` instances for UUID columns; SQLite
        # returns ``str``. The construction helper normalizes both shapes so
        # the record's ``id`` / ``hypothesis_id`` are always ``str``.
        row_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        hypothesis_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        row: dict[str, Any] = {
            "id": row_id,
            "hypothesis_id": hypothesis_id,
            "oracle_id": "oracle-1",
            "correlation_id": "corr-1",
            "timestamp": 1000,
            "t_oracle": 0.5,
            "c_oracle_raw": 0.5,
            "c_oracle_discounted": 0.25,
            "c_herd": 0.3,
            "n_oracle_prior": 0,
        }

        records = build_attestation_records(rows=[row])

        assert len(records) == 1
        assert isinstance(records[0].id, str)
        assert isinstance(records[0].hypothesis_id, str)
        assert records[0].id == str(row_id)
        assert records[0].hypothesis_id == str(hypothesis_id)


def _valid_result(**overrides: object) -> HypothesisResult:
    defaults: dict[str, object] = {
        "id": "00000000-0000-0000-0000-000000000001",
        "content": "Service X uses gRPC",
        "created_at": 1000,
        "score": 0.4,
        "proximity": 0.5,
    }
    defaults.update(overrides)
    return HypothesisResult(**defaults)  # pyright: ignore[reportArgumentType]  # same rationale as above


class TestHypothesisResultConstruction:
    """HypothesisResult extends HypothesisRecord with retrieval scores from two-lane search.

    Construction does not validate score/proximity bounds: those are enforced by
    the SQL RRF formula and DB CHECK constraints. The hot path uses
    ``model_construct()``, so field validators here would be dead code.
    """

    def test_hypothesis_result_accepts_score_at_boundaries(self) -> None:
        r = _valid_result(score=0.0)
        assert r.score == 0.0
        r = _valid_result(score=1.0)
        assert r.score == 1.0

    def test_hypothesis_result_accepts_proximity_at_minus_one(self) -> None:
        # Antiparallel cosine: opposite-direction vectors. Honest, allowed.
        r = _valid_result(proximity=-1.0)
        assert r.proximity == -1.0

    def test_hypothesis_result_proximity_defaults_to_zero(self) -> None:
        # 0.0 is the honest "no signal" default for authority-only rows.
        r = HypothesisResult.model_validate(
            {
                "id": "00000000-0000-0000-0000-000000000002",
                "content": "x",
                "created_at": 0,
                "score": 0.5,
            }
        )
        assert r.proximity == 0.0

    def test_hypothesis_result_construction_accepts_out_of_range_score_and_proximity(
        self,
    ) -> None:
        r = _valid_result(score=2.0, proximity=-5.0)
        assert r.score == 2.0
        assert r.proximity == -5.0


def _valid_request(**overrides: object) -> RequestRecord:
    defaults: dict[str, object] = {
        "id": "req-1",
        "oracle_id": "sub:oracle-A",
        "timestamp": 1000,
        "question": "what is X?",
        "context": "why I'm asking",
        "hypothesis": "service X uses gRPC",
        "reasoning": "grep showed gRPC imports",
        "confidence": 0.5,
    }
    defaults.update(overrides)
    # Builder pattern: values are runtime-correct per field, but the dict is typed
    # dict[str, object]. See _valid_attestation above for the same rationale. The
    # pyright ignore below covers the **kwargs splat.
    return RequestRecord(**defaults)  # pyright: ignore[reportArgumentType]


class TestRequestRecordValidation:
    def test_request_record_accepts_valid_data(self) -> None:
        r = _valid_request()
        assert r.id == "req-1"
        assert r.oracle_id == "sub:oracle-A"
        assert r.timestamp == 1000
        assert r.question == "what is X?"
        assert r.confidence == 0.5

    def test_request_record_rejects_empty_id(self) -> None:
        with pytest.raises(ValueError, match="id"):
            _valid_request(id="")

    def test_request_record_rejects_empty_oracle_id(self) -> None:
        with pytest.raises(ValueError, match="oracle_id"):
            _valid_request(oracle_id="")

    def test_request_record_rejects_negative_timestamp(self) -> None:
        with pytest.raises(ValueError, match="timestamp"):
            _valid_request(timestamp=-1)

    def test_request_record_accepts_zero_timestamp(self) -> None:
        r = _valid_request(timestamp=0)
        assert r.timestamp == 0

    def test_request_record_rejects_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            _valid_request(confidence=1.5)
        with pytest.raises(ValueError, match="confidence"):
            _valid_request(confidence=-1.5)

    def test_request_record_rejects_confidence_nan(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            _valid_request(confidence=float("nan"))

    def test_request_record_rejects_confidence_inf(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            _valid_request(confidence=float("inf"))

    def test_request_record_accepts_confidence_none(self) -> None:
        r = _valid_request(confidence=None)
        assert r.confidence is None

    def test_request_record_accepts_zero_confidence(self) -> None:
        r = _valid_request(confidence=0.0)
        assert r.confidence == 0.0

    def test_request_record_accepts_all_content_fields_none(self) -> None:
        """Storage layer does not enforce the at-least-one rule."""
        r = _valid_request(
            question=None,
            context=None,
            hypothesis=None,
            reasoning=None,
            confidence=None,
        )
        assert r.question is None
        assert r.context is None
        assert r.hypothesis is None
        assert r.reasoning is None
        assert r.confidence is None

    def test_request_record_is_frozen(self) -> None:
        r = _valid_request()
        with pytest.raises(ValidationError, match="frozen"):
            r.question = "mutated"  # pyright: ignore[reportAttributeAccessIssue]


def _valid_cache_entry(**overrides: object) -> CacheEntry:
    defaults: dict[str, object] = {
        "collection": "oauth-client-registrations",
        "key": "client-abc",
        "value": '{"token": "opaque"}',
        "created_at": 1000,
        "expires_at": 2000,
    }
    defaults.update(overrides)
    # Builder pattern: same rationale as _valid_attestation above. The pyright
    # ignore below covers the **kwargs splat.
    return CacheEntry(**defaults)  # pyright: ignore[reportArgumentType]


class TestCacheEntry:
    def test_cache_entry_round_trips_all_fields(self) -> None:
        r = _valid_cache_entry()
        assert r.collection == "oauth-client-registrations"
        assert r.key == "client-abc"
        assert r.value == '{"token": "opaque"}'
        assert r.created_at == 1000
        assert r.expires_at == 2000

    def test_cache_entry_rejects_empty_collection(self) -> None:
        with pytest.raises(ValueError, match="collection"):
            _valid_cache_entry(collection="")

    def test_cache_entry_rejects_empty_key(self) -> None:
        with pytest.raises(ValueError, match="key"):
            _valid_cache_entry(key="")

    def test_cache_entry_rejects_negative_created_at(self) -> None:
        with pytest.raises(ValueError, match="created_at"):
            _valid_cache_entry(created_at=-1)

    def test_cache_entry_rejects_negative_expires_at(self) -> None:
        with pytest.raises(ValueError, match="expires_at"):
            _valid_cache_entry(expires_at=-1)

    def test_cache_entry_allows_none_expires_at(self) -> None:
        r = _valid_cache_entry(expires_at=None)
        assert r.expires_at is None
