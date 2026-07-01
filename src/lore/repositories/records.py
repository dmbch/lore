"""Repository record types, construction helpers, and value objects.

Pydantic models (frozen) that mirror the database schema and double as the
orchestrator-visible shape for stored entities. Each record validates on
construction, mirroring DB constraints (NOT NULL, type ranges, value
domains) so corrupt data fails loudly at construction, not downstream.
Hot-path reads use ``model_construct()`` to skip validation since the
database already enforces the same constraints.
"""

import math
import uuid
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

# Row dicts come from database cursors (aiosqlite Row or psycopg dict_row).
# The actual value types are driver-determined: Any is unavoidable here.
type _Row = dict[str, Any]


class HypothesisRecord(BaseModel):
    """A stored hypothesis. No embedding, no epistemic state."""

    model_config = ConfigDict(frozen=True, strict=True)

    id: str
    content: str
    created_at: int

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError:
            msg = f"id must be a valid UUID, got {v!r}"
            raise ValueError(msg) from None
        return v

    @field_validator("content")
    @classmethod
    def _validate_content(cls, v: str) -> str:
        if not v:
            msg = "content must be non-empty"
            raise ValueError(msg)
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, v: int) -> int:
        if v < 0:
            msg = f"created_at must be >= 0, got {v}"
            raise ValueError(msg)
        return v


class HypothesisResult(HypothesisRecord):
    """A hypothesis with retrieval scores from two-lane search.

    ``score`` is the composite RRF score in ``[0, 1]`` (Cormack et al. 2009);
    per-lane RRF intermediates are computed in SQL but not surfaced here:
    no caller consumes them. ``proximity`` is the raw cosine similarity in
    ``[-1, 1]`` (1 - cosine_distance), defaulting to 0.0 for rows that did
    not surface in the proximity lane. 0.0 is the "no signal" default for
    authority-only rows; negative values are reserved for genuine vector
    dissimilarity.

    Bounds are enforced by the SQL RRF formula (``1/(k+rank)``, k=60, so each
    lane contributes in ``(0, 1/61]``; the weighted sum stays in ``[0, 1]``)
    and by DB CHECK constraints on the ``hypotheses`` table, not by Pydantic.
    Reads use ``model_construct()`` on the hot path, so any field validator on
    this class would be dead code.
    """

    score: float
    proximity: float = 0.0


class AttestationRecord(BaseModel):
    """A stored ledger entry. Schema mirrors the ledger table: see IDEA.md §The Ledger."""

    model_config = ConfigDict(frozen=True, strict=True)

    id: str
    hypothesis_id: str
    oracle_id: str
    correlation_id: str
    timestamp: int
    t_oracle: float
    c_oracle_raw: float
    c_oracle_discounted: float
    c_herd: float
    n_oracle_prior: int

    @field_validator("id", "hypothesis_id")
    @classmethod
    def _validate_uuid_fields(cls, v: str, info: ValidationInfo) -> str:
        field_name = info.field_name
        try:
            uuid.UUID(v)
        except ValueError:
            msg = f"{field_name} must be a valid UUID, got {v!r}"
            raise ValueError(msg) from None
        return v

    @field_validator("oracle_id", "correlation_id")
    @classmethod
    def _validate_non_empty_fields(cls, v: str, info: ValidationInfo) -> str:
        if not v:
            field_name = info.field_name
            msg = f"{field_name} must be non-empty"
            raise ValueError(msg)
        return v

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, v: int) -> int:
        if v < 0:
            msg = f"timestamp must be >= 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("t_oracle")
    @classmethod
    def _validate_t_oracle(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0.0 or v > 1.0:
            msg = f"t_oracle must be in [0, 1], got {v}"
            raise ValueError(msg)
        return v

    @field_validator("c_oracle_raw", "c_oracle_discounted", "c_herd")
    @classmethod
    def _validate_confidence_fields(cls, v: float, info: ValidationInfo) -> float:
        # Storage bounds: [-1, 1], the mathematical domain for a confidence scalar.
        # Trust discounting (P_effective < 1 for K >= 1) is the pipeline policy that
        # prevents dogmatic opinions from reaching ECBF. The storage layer only rejects
        # values outside the mathematical domain.
        if not math.isfinite(v) or v < -1.0 or v > 1.0:
            field_name = info.field_name
            msg = f"{field_name} must be in [-1, 1], got {v}"
            raise ValueError(msg)
        return v

    @field_validator("n_oracle_prior")
    @classmethod
    def _validate_n_oracle_prior(cls, v: int) -> int:
        if v < 0:
            msg = f"n_oracle_prior must be >= 0, got {v}"
            raise ValueError(msg)
        return v


class RequestRecord(BaseModel):
    """A stored request. One row per consult call.

    Structured columns mirror the ``ConsultLoreRequest`` payload plus the
    bookkeeping fields (``id``, ``oracle_id``, ``timestamp``). The
    ``hypothesis`` column is the **raw, pre-Interpreter string** the oracle
    submitted, distinct from the ``hypotheses`` table, which stores atomic,
    Interpreter-decomposed propositions. Content fields are nullable at the
    storage layer; the at-least-one rule is enforced one layer up at the
    domain boundary.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    id: str  # = correlation_id; FK target for attestations
    oracle_id: str
    timestamp: int
    question: str | None = None
    context: str | None = None
    hypothesis: str | None = None
    reasoning: str | None = None
    confidence: float | None = None

    @field_validator("id", "oracle_id")
    @classmethod
    def _validate_non_empty_fields(cls, v: str, info: ValidationInfo) -> str:
        if not v:
            field_name = info.field_name
            msg = f"{field_name} must be non-empty"
            raise ValueError(msg)
        return v

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, v: int) -> int:
        if v < 0:
            msg = f"timestamp must be >= 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: float | None) -> float | None:
        # Storage bounds: [-1, 1], the mathematical domain for a confidence
        # scalar. The math service enforces the tighter epistemic policy
        # downstream. ``None`` is the genuine "no confidence submitted" signal
        # and passes through unchanged.
        if v is None:
            return v
        if not math.isfinite(v) or v < -1.0 or v > 1.0:
            msg = f"confidence must be in [-1, 1], got {v}"
            raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# Record construction helpers: used by backend implementations, not exported.
# ---------------------------------------------------------------------------


def generate_id() -> str:
    return str(uuid.uuid4())


def build_attestation_records(*, rows: Iterable[_Row]) -> list[AttestationRecord]:
    """Build AttestationRecords from flat query rows.

    Postcondition: ``id`` and ``hypothesis_id`` are returned as ``str``;
    ``correlation_id`` and ``oracle_id`` pass through unchanged.

    Uses model_construct() to skip validation: the database already enforces
    constraints, so re-validating on read is redundant work.

    psycopg returns ``uuid.UUID`` for UUID columns; SQLite returns ``str``.
    ``str()`` is idempotent on strings and canonical on UUID, so the coercion
    is safe on both backends. ``correlation_id`` is TEXT on both backends.
    """
    return [
        AttestationRecord.model_construct(
            id=str(r["id"]),
            hypothesis_id=str(r["hypothesis_id"]),
            oracle_id=r["oracle_id"],
            correlation_id=r["correlation_id"],
            timestamp=r["timestamp"],
            t_oracle=r["t_oracle"],
            c_oracle_raw=r["c_oracle_raw"],
            c_oracle_discounted=r["c_oracle_discounted"],
            c_herd=r["c_herd"],
            n_oracle_prior=r["n_oracle_prior"],
        )
        for r in rows
    ]
