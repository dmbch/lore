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
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from pydantic import NonNegativeInt, ValidationInfo, field_validator

from lore._pydantic import DataModel, NonEmptyStr, SignedUnitInterval, UnitInterval
from lore.domain import EvidenceInput

# Row dicts come from database cursors (aiosqlite Row or psycopg dict_row).
# The actual value types are driver-determined: Any is unavoidable here.
type _Row = dict[str, Any]


class HypothesisRecord(DataModel):
    """A stored hypothesis. No embedding, no epistemic state."""

    id: str
    content: NonEmptyStr
    created_at: NonNegativeInt

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError:
            msg = f"id must be a valid UUID, got {v!r}"
            raise ValueError(msg) from None
        return v


class DecayWindow(NamedTuple):
    """Closed fetch window ``[start, t_now]`` for decay-weighted ledger reads.

    ``t_now`` and ``half_life`` travel together by construction: a lone
    value is unrepresentable, so fetches need no pairing check.
    """

    t_now: int
    half_life: float

    @property
    def start(self) -> int:
        """Lower bound of the 5-half-life fetch window.

        ``math.isfinite`` guards the SQL boundary: ``half_life=inf`` is the
        "no decay" mode, but ``int(5 * inf)`` raises OverflowError and
        ``int(5 * nan)`` raises ValueError. Either way the bound collapses
        to zero, so every Unix-epoch row is in scope.
        """
        return self.t_now - int(5 * self.half_life) if math.isfinite(self.half_life) else 0


class LedgerView(NamedTuple):
    """One hypothesis's ledger: the requested rows plus an exact summary.

    ``rows`` honors the caller's decay window when one is given.
    ``oracle_count`` counts distinct attesting oracles, the synthetic
    transfer carrier among them: the same admit-the-carrier policy
    maturity and the witness rule take. It and ``last_attested`` are
    always full-history, so an all-stale ledger stays distinguishable
    from a never-attested one even when the windowed rows are empty.
    """

    rows: list[AttestationRecord]
    oracle_count: int
    last_attested: int | None


class HypothesisResult(HypothesisRecord):
    """A hypothesis with retrieval scores from two-lane search.

    ``score`` is the composite RRF score in ``[0, 1]`` (Cormack et al. 2009);
    per-lane RRF intermediates are computed in SQL but not surfaced here:
    no caller consumes them. ``proximity`` is the raw cosine similarity in
    ``[-1, 1]`` up to engine float noise (1 - cosine_distance; the enrich
    stage clamps), defaulting to 0.0 for rows that did not surface in the
    proximity lane. 0.0 is the "no signal" default for authority-only rows;
    negative values are reserved for genuine vector dissimilarity.

    Bounds are enforced by the SQL RRF formula (``1/(k+rank)``, k=60, so each
    lane contributes in ``(0, 1/61]``; the weighted sum stays in ``[0, 1]``)
    and by DB CHECK constraints on the ``hypotheses`` table, not by Pydantic.
    Reads use ``model_construct()`` on the hot path, so any field validator on
    this class would be dead code.
    """

    score: float
    proximity: float = 0.0


class AttestationRecord(DataModel):
    """A stored ledger entry. Schema mirrors the ledger table: see IDEA.md §The Ledger."""

    id: str
    hypothesis_id: str
    oracle_id: NonEmptyStr
    correlation_id: NonEmptyStr
    timestamp: NonNegativeInt
    t_oracle: UnitInterval
    # Storage bounds: [-1, 1], the mathematical domain for a confidence scalar.
    # Trust discounting (P_effective < 1 for K >= 1) is the pipeline policy that
    # prevents dogmatic opinions from reaching ECBF. The storage layer only rejects
    # values outside the mathematical domain.
    c_oracle_raw: SignedUnitInterval
    c_oracle_discounted: SignedUnitInterval
    c_herd: SignedUnitInterval
    n_oracle_prior: NonNegativeInt

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


class RequestRecord(DataModel):
    """A stored request. One row per consult call.

    Structured columns mirror the ``ConsultLoreRequest`` payload plus the
    bookkeeping fields (``id``, ``oracle_id``, ``timestamp``). The
    ``hypothesis`` column is the **raw, pre-Interpreter string** the oracle
    submitted, distinct from the ``hypotheses`` table, which stores atomic,
    Interpreter-decomposed propositions. Content fields are nullable at the
    storage layer; the at-least-one rule is enforced one layer up at the
    domain boundary.
    """

    id: NonEmptyStr  # = correlation_id; FK target for attestations
    oracle_id: NonEmptyStr
    timestamp: NonNegativeInt
    question: str | None = None
    context: str | None = None
    hypothesis: str | None = None
    reasoning: str | None = None
    # Storage bounds: [-1, 1], the mathematical domain for a confidence
    # scalar. The math service enforces the tighter epistemic policy
    # downstream. ``None`` is the genuine "no confidence submitted" signal
    # and passes through unchanged.
    confidence: SignedUnitInterval | None = None


class CacheEntry(DataModel):
    """A stored key-value row: operational cache state, not epistemic evidence.

    Serves OAuth client registrations, upstream tokens, and MCP session
    state, isolated by ``collection``. ``value`` is an opaque JSON blob
    (ciphertext where encryption wraps the store). Timestamps are epoch
    seconds, matching every other timestamp column in the schema;
    ``expires_at`` is ``None`` for entries without TTL.
    """

    collection: NonEmptyStr
    key: NonEmptyStr
    value: str
    created_at: NonNegativeInt
    expires_at: NonNegativeInt | None = None


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


def build_ledger_views(
    *,
    hypothesis_ids: Sequence[str],
    rows: Iterable[_Row],
    stats: Mapping[str, tuple[int, int]],
) -> dict[str, LedgerView]:
    """Assemble per-hypothesis views from fetched rows and full-history stats.

    ``stats`` maps hypothesis id to ``(oracle_count, last_attested)``
    over the whole ledger; ids absent from it were never attested. Every
    requested ID is present in the result.
    """
    grouped: dict[str, list[AttestationRecord]] = {hid: [] for hid in hypothesis_ids}
    for record in build_attestation_records(rows=rows):
        grouped[record.hypothesis_id].append(record)
    return {
        hid: LedgerView(
            rows=grouped[hid],
            oracle_count=stats[hid][0] if hid in stats else 0,
            last_attested=stats[hid][1] if hid in stats else None,
        )
        for hid in hypothesis_ids
    }


def group_evidence_rows(
    *, hypothesis_ids: Sequence[str], rows: Iterable[_Row]
) -> dict[str, list[EvidenceInput]]:
    """Group fetched evidence rows per requested hypothesis, keys always present.

    ``str()`` on ``hypothesis_id`` mirrors ``build_attestation_records``'
    UUID-vs-str normalisation so keys match the input IDs on both backends.
    """
    result: dict[str, list[EvidenceInput]] = {hid: [] for hid in hypothesis_ids}
    for row in rows:
        result[str(row["hypothesis_id"])].append(
            EvidenceInput(
                c_oracle_discounted=row["c_oracle_discounted"],
                timestamp=row["timestamp"],
            )
        )
    return result
