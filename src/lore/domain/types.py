"""Cross-layer types for the consult execution loop, in pipeline order.

Leaf dependency among the layers: every layer may import from here; this
module imports nothing but ``lore._pydantic``, layer zero beneath every layer.
"""

from datetime import date
from typing import NamedTuple, Self

from pydantic import Field, NonNegativeInt, field_validator, model_validator

from lore._pydantic import DataModel, NonEmptyStr, SignedUnitInterval, UnitInterval

# --- Identity ---


LOCAL_ORACLE = "_local"
"""Synthetic oracle ID for unauthenticated topologies (stdio dev, proxy-trusted HTTP)."""

TRANSFER_ORACLE = "_transfer"
"""Synthetic oracle ID for epistemic transfer attestations."""


# --- MCP boundary ---


class ConsultLoreRequest(DataModel):
    """MCP input for consult, all fields optional."""

    question: str | None = None
    context: str | None = None
    hypothesis: str | None = None
    reasoning: str | None = None
    confidence: SignedUnitInterval | None = None

    @field_validator("question", "context", "hypothesis", "reasoning", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        # A whitespace-only field is no input: fold it to None so the gate below
        # sees it as absent. A field with real content passes verbatim.
        if isinstance(v, str) and not v.strip():
            return None
        return v

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


class ConsultLoreResponse(DataModel):
    """MCP output: the Archivist's synthesized answer."""

    answer: str


# --- Interpret stage ---


class InterpreterInput(DataModel):
    """Passthrough from MCP request to the Interpreter, plus the consult date."""

    question: str | None = None
    hypothesis: str | None = None
    context: str | None = None
    reasoning: str | None = None
    # UTC calendar date of the consult; MCP carries no client timezone.
    today: date


class InterpreterOutput(DataModel):
    """Interpreter result: normalized question, decomposed propositions, keywords."""

    question: str | None = Field(
        default=None, description="Normalized question text for consistent embedding"
    )
    propositions: list[str] = Field(
        default_factory=list,
        max_length=16,  # the original plus the 15-atom cap in interpreter.md step 5
        description=(
            "The normalized, grounded, date-resolved hypothesis first,"
            " then atoms if it is a genuine conjunction"
        ),
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Full-text search keywords from all populated input fields, most specific first"
        ),
    )


# --- Retrieve stage ---


class SearchResult(DataModel):
    """Retrieval candidate with scores and epistemic snapshot.

    ``score`` is the composite RRF score in ``[0, 1]`` from two-lane search.
    ``proximity`` is the raw cosine similarity in ``[-1, 1]`` from the
    proximity lane, defaulting to 0.0 for rows that surfaced authority-only.
    ``last_attested`` is the UTC calendar date of the newest attestation,
    ``None`` when the hypothesis has never been attested.
    """

    id: str
    content: str
    c_herd: SignedUnitInterval
    attestation_count: NonNegativeInt
    last_attested: date | None
    score: UnitInterval
    proximity: SignedUnitInterval = 0.0


# --- Observe stage ---


class FrontierEntry(DataModel):
    """One uncertainty-frontier row: a hypothesis with its current epistemic snapshot.

    ``c_herd`` is the projected herd consensus scalar in ``[-1, 1]``;
    ``uncertainty`` is the projected uncertainty in ``[0, 1]``.
    ``last_attested`` is the UTC calendar date of the newest attestation,
    ``None`` when the hypothesis has never been attested.
    """

    id: str
    content: str
    c_herd: SignedUnitInterval
    uncertainty: UnitInterval
    attestation_count: NonNegativeInt
    last_attested: date | None


# --- Reason stage ---


class ArchivistInput(DataModel):
    """Archivist input, unified for both read and write paths."""

    question: str | None = None
    hypothesis: str | None = None
    context: str | None = None
    reasoning: str | None = None
    propositions: list[str] = Field(default_factory=list)
    retrieved: list[SearchResult]
    # UTC calendar date of the consult; MCP carries no client timezone.
    today: date


class Resolution(DataModel):
    """Proposition-centric resolution: one per inbound proposition.

    Exactly one primary is set:
    - `corroborates`: paraphrase of an existing hypothesis (its ID).
    - `contributes`: novel content entering the archive.

    `contradicts` may pair with either form: IDs of existing hypotheses
    the proposition is mutually exclusive with.
    """

    corroborates: NonEmptyStr | None = Field(
        default=None, description="Existing hypothesis ID this proposition paraphrases"
    )
    contributes: NonEmptyStr | None = Field(
        default=None, description="Novel proposition content entering the archive"
    )
    contradicts: list[NonEmptyStr] = Field(
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


class ArchivistOutput(DataModel):
    """Archivist output, unified for both read and write paths."""

    reasoning: str = Field(
        description="Step-by-step analysis of the consult against the retrieved knowledge"
    )
    answer: str = Field(
        description="Direct answer to the consult, grounded in the herd's knowledge"
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


class TrustSignal(DataModel):
    """One attestation's alignment context for oracle trust computation.

    Cross-layer boundary type: the repository produces these from SQL window
    functions, the math service consumes them. Fields match docs/logic.md,
    Oracle Trust section.

    Validated on construction (boundary type). The DB enforces matching
    bounds via CHECK constraints on the underlying ``attestations`` columns
    (``c_oracle_raw``, ``c_herd``); the constructor is a second line of
    defence against rows that bypass those constraints.
    """

    c_oracle_raw: SignedUnitInterval
    timestamp: NonNegativeInt
    c_herd_prior: SignedUnitInterval
    c_herd_now: SignedUnitInterval
    n_oracle_prior: NonNegativeInt


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


class WriteContext(DataModel):
    """Per-consult write coordinates threaded verbatim through every attestation."""

    oracle_id: NonEmptyStr
    correlation_id: NonEmptyStr
    confidence: SignedUnitInterval
    t_now: NonNegativeInt
