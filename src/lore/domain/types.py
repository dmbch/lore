"""Cross-layer types for the consult execution loop, in pipeline order.

Leaf dependency in the import graph: every layer may import from here; this
module imports from nothing in the project.
"""

import math
from datetime import date
from typing import NamedTuple, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

# --- Identity ---


LOCAL_ORACLE = "_local"
"""Synthetic oracle ID for unauthenticated topologies (stdio dev, proxy-trusted HTTP)."""

TRANSFER_ORACLE = "_transfer"
"""Synthetic oracle ID for epistemic transfer attestations."""


def _check_confidence(*, value: float, field_name: str | None) -> float:
    if not math.isfinite(value) or value < -1.0 or value > 1.0:
        msg = f"{field_name} must be in [-1, 1], got {value}"
        raise ValueError(msg)
    return value


# --- MCP boundary ---


class ConsultLoreRequest(BaseModel):
    """MCP input for consult, all fields optional."""

    model_config = ConfigDict(frozen=True, strict=True)

    question: str | None = None
    context: str | None = None
    hypothesis: str | None = None
    reasoning: str | None = None
    confidence: float | None = None

    @model_validator(mode="after")
    def _require_question_or_complete_hypothesis(self) -> Self:
        if self.question is None and self.hypothesis is None:
            msg = "consult requires a question, a hypothesis, or both"
            raise ValueError(msg)
        if self.hypothesis is not None and self.confidence is None:
            msg = "consult with a hypothesis also requires a confidence scalar"
            raise ValueError(msg)
        if self.confidence is not None and self.hypothesis is None:
            msg = "consult with a confidence scalar also requires a hypothesis"
            raise ValueError(msg)
        return self

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: float | None, info: ValidationInfo) -> float | None:
        if v is not None:
            _check_confidence(value=v, field_name=info.field_name)
        return v


class ConsultLoreResponse(BaseModel):
    """MCP output: the Archivist's synthesized answer."""

    model_config = ConfigDict(frozen=True, strict=True)

    answer: str


# --- Interpret stage ---


class InterpreterInput(BaseModel):
    """Passthrough from MCP request to the Interpreter, plus the consult date."""

    model_config = ConfigDict(frozen=True, strict=True)

    question: str | None = None
    hypothesis: str | None = None
    context: str | None = None
    reasoning: str | None = None
    # UTC calendar date of the consult; MCP carries no client timezone.
    today: date


class InterpreterOutput(BaseModel):
    """Interpreter result: normalized question, decomposed propositions, keywords."""

    model_config = ConfigDict(frozen=True, strict=True)

    question: str | None = Field(
        default=None, description="Normalized question text for consistent embedding"
    )
    propositions: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Normalized original hypothesis first, then atomic decompositions if composite",
    )
    keywords: list[str] = Field(
        default_factory=list, description="Retrieval keywords extracted from the content"
    )


# --- Retrieve stage ---


class SearchResult(BaseModel):
    """Retrieval candidate with scores and epistemic snapshot.

    ``score`` is the composite RRF score in ``[0, 1]`` from two-lane search.
    ``proximity`` is the raw cosine similarity in ``[-1, 1]`` from the
    proximity lane, defaulting to 0.0 for rows that surfaced authority-only.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    id: str
    content: str
    c_herd: float
    attestation_count: int
    last_attested: int
    score: float
    proximity: float = 0.0

    @field_validator("c_herd")
    @classmethod
    def _validate_c_herd(cls, v: float, info: ValidationInfo) -> float:
        return _check_confidence(value=v, field_name=info.field_name)

    @field_validator("score")
    @classmethod
    def _validate_score(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0.0 or v > 1.0:
            msg = f"score must be in [0, 1], got {v}"
            raise ValueError(msg)
        return v

    @field_validator("proximity")
    @classmethod
    def _validate_proximity(cls, v: float) -> float:
        if not math.isfinite(v) or v < -1.0 or v > 1.0:
            msg = f"proximity must be in [-1, 1], got {v}"
            raise ValueError(msg)
        return v

    @field_validator("attestation_count", "last_attested")
    @classmethod
    def _validate_non_negative_int(cls, v: int, info: ValidationInfo) -> int:
        if v < 0:
            msg = f"{info.field_name} must be >= 0, got {v}"
            raise ValueError(msg)
        return v


# --- Reason stage ---


class ArchivistInput(BaseModel):
    """Archivist input, unified for both read and write paths."""

    model_config = ConfigDict(frozen=True, strict=True)

    question: str | None = None
    hypothesis: str | None = None
    context: str | None = None
    reasoning: str | None = None
    propositions: list[str] = Field(default_factory=list)
    retrieved: list[SearchResult]


class Resolution(BaseModel):
    """Proposition-centric resolution: one per inbound proposition.

    Exactly one primary is set:
    - `corroborates`: paraphrase of an existing hypothesis (its ID).
    - `contributes`: novel content entering the archive.

    `contradicts` may pair with either form: IDs of existing hypotheses
    the proposition is mutually exclusive with.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    corroborates: str | None = Field(
        default=None, description="Existing hypothesis ID this proposition paraphrases"
    )
    contributes: str | None = Field(
        default=None, description="Novel proposition content entering the archive"
    )
    contradicts: list[str] = Field(
        default_factory=list, description="IDs of contradicted hypotheses"
    )

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        has_corroborates = self.corroborates is not None
        has_contributes = self.contributes is not None
        if has_corroborates == has_contributes:
            msg = "exactly one of corroborates or contributes must be set"
            raise ValueError(msg)
        if has_corroborates and self.corroborates in self.contradicts:
            msg = "corroborates cannot also appear in contradicts"
            raise ValueError(msg)
        if len(self.contradicts) != len(set(self.contradicts)):
            msg = "contradicts must not contain duplicate IDs"
            raise ValueError(msg)
        return self

    @field_validator("corroborates")
    @classmethod
    def _validate_corroborates(cls, v: str | None) -> str | None:
        if v is not None and not v:
            msg = "corroborates must be non-empty"
            raise ValueError(msg)
        return v

    @field_validator("contributes")
    @classmethod
    def _validate_contributes(cls, v: str | None) -> str | None:
        if v is not None and not v:
            msg = "contributes must be non-empty"
            raise ValueError(msg)
        return v

    @field_validator("contradicts")
    @classmethod
    def _validate_contradicts(cls, v: list[str]) -> list[str]:
        for id_ in v:
            if not id_:
                msg = "all IDs in contradicts must be non-empty strings"
                raise ValueError(msg)
        return v


class ArchivistOutput(BaseModel):
    """Archivist output, unified for both read and write paths."""

    model_config = ConfigDict(frozen=True, strict=True)

    reasoning: str = Field(
        description="Step-by-step analysis of how propositions relate to existing knowledge"
    )
    answer: str = Field(
        description="Synthesized explanation of how the input relates to existing knowledge"
    )
    resolutions: list[Resolution] = Field(
        # Subscripted factory: pyright strict cannot infer the element type
        # from plain ``list`` for custom-model fields. ``list[str]`` siblings
        # in this file get away with plain ``list`` because str is a builtin.
        default_factory=list[Resolution],
        description="Proposition-centric resolutions",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Free-text classification challenges: observability surface, not stored",
    )

    @model_validator(mode="after")
    def _disjoint_resolution_ids(self) -> Self:
        seen: set[str] = set()
        for r in self.resolutions:
            ids: list[str] = list(r.contradicts)
            if r.corroborates is not None:
                ids.append(r.corroborates)
            for h_id in ids:
                if h_id in seen:
                    msg = (
                        "duplicate hypothesis ID across resolutions: "
                        f"{h_id!r} appears in multiple corroborates/contradicts slots"
                    )
                    raise ValueError(msg)
                seen.add(h_id)
        return self

    @model_validator(mode="after")
    def _disjoint_resolution_novels(self) -> Self:
        """Reject literally-identical contributes strings across resolutions.

        Exact-match only: whitespace and case differences are not normalized.
        Near-duplicates surface naturally as two embeddings in retrieval; the
        validator catches the trivial collapse failure mode, not semantic
        near-equality.
        """
        seen: set[str] = set()
        for r in self.resolutions:
            if r.contributes is None:
                continue
            if r.contributes in seen:
                msg = (
                    "duplicate novel content across resolutions: "
                    f"{r.contributes!r} appears in multiple contributes slots"
                )
                raise ValueError(msg)
            seen.add(r.contributes)
        return self


# --- Record stage / math wire ---


class TrustSignal(BaseModel):
    """One attestation's alignment context for oracle trust computation.

    Cross-layer boundary type: the repository produces these from SQL window
    functions, the math service consumes them. Fields match docs/logic.md,
    Oracle Trust section.

    Validated on construction (boundary type). The DB enforces matching
    bounds via CHECK constraints on the underlying ``attestations`` columns
    (``c_oracle_raw``, ``c_herd``); the constructor is a second line of
    defence against rows that bypass those constraints.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    c_oracle_raw: float
    timestamp: int
    c_herd_prior: float
    c_herd_now: float
    n_oracle_prior: int

    @field_validator("c_oracle_raw", "c_herd_prior", "c_herd_now")
    @classmethod
    def _validate_confidence(cls, v: float, info: ValidationInfo) -> float:
        return _check_confidence(value=v, field_name=info.field_name)

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, v: int) -> int:
        if v < 0:
            msg = f"timestamp must be >= 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("n_oracle_prior")
    @classmethod
    def _validate_n_oracle_prior(cls, v: int) -> int:
        if v < 0:
            msg = f"n_oracle_prior must be >= 0, got {v}"
            raise ValueError(msg)
        return v


class EvidenceInput(NamedTuple):
    """What the math service needs from an existing attestation."""

    c_oracle_discounted: float
    timestamp: int


class AttestationComputed(NamedTuple):
    """What the math service computes for a new attestation.

    The orchestrator combines these computed fields with identity fields
    (attestation_id, hypothesis_id, oracle_id, correlation_id) to build
    a complete ledger record.
    """

    t_oracle: float
    c_oracle_raw: float
    c_oracle_discounted: float
    c_herd: float


class WriteContext(BaseModel):
    """Per-consult write coordinates threaded verbatim through every attestation."""

    model_config = ConfigDict(frozen=True, strict=True)

    oracle_id: str
    correlation_id: str
    confidence: float
    t_now: int

    @field_validator("oracle_id", "correlation_id")
    @classmethod
    def _validate_non_empty(cls, v: str, info: ValidationInfo) -> str:
        if not v:
            msg = f"{info.field_name} must be non-empty"
            raise ValueError(msg)
        return v

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: float, info: ValidationInfo) -> float:
        return _check_confidence(value=v, field_name=info.field_name)

    @field_validator("t_now")
    @classmethod
    def _validate_t_now(cls, v: int) -> int:
        if v < 0:
            msg = f"t_now must be >= 0, got {v}"
            raise ValueError(msg)
        return v
