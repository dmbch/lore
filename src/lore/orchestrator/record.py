"""Record stage — transaction-scoped writes.

``record`` is the orchestrator-facing entry point: it runs trust grading,
fetches a snapshot-consistent attestation map, and dispatches each Resolution
to a ``Recorder`` instance bound to the caller's transaction.
"""

import hashlib
from typing import TYPE_CHECKING

import structlog

from lore.domain import (
    TRANSFER_ORACLE,
    ArchivistOutput,
    AttestationComputed,
    EvidenceInput,
    Resolution,
)
from lore.math import MathService
from lore.repositories import AttestationRecord, Repositories
from lore.telemetry import start_span

if TYPE_CHECKING:
    from lore.config import LoreSettings
    from lore.repositories import AttestationsRepository, HypothesisRepository

log = structlog.get_logger(__name__)


async def record(
    *,
    repos: Repositories,
    math: MathService,
    reasoned: ArchivistOutput,
    novel_embeddings: dict[str, list[float]],
    oracle_id: str,
    correlation_id: str,
    confidence: float,
    t_now: int,
    settings: LoreSettings,
) -> None:
    """Trust scan, attestation refetch, and writes — all inside the caller's
    ``pool.transaction()``. The trust pipeline (maturity, ECBF) needs a
    snapshot-consistent attestation map; the read-path ``enrich`` snapshot
    feeds only the Archivist."""
    target_ids = _resolution_target_ids(reasoned.resolutions)
    with start_span("lore.record"):
        alignments = await repos.attestations.fetch_trust_alignments(
            oracle_id=oracle_id,
            t_now=t_now,
            trust_half_life=settings.decay.trust,
        )
        t_oracle = math.compute_oracle_trust(rows=alignments, t_now=t_now)

        attestation_map = (
            await repos.attestations.find_by_hypotheses(list(target_ids)) if target_ids else {}
        )

        recorder = Recorder(
            hypotheses=repos.hypotheses,
            attestations=repos.attestations,
            math=math,
            reasoned=reasoned,
            attestation_map=attestation_map,
            novel_embeddings=novel_embeddings,
            oracle_id=oracle_id,
            correlation_id=correlation_id,
            confidence=confidence,
            t_oracle=t_oracle,
            t_now=t_now,
            settings=settings,
        )

        await recorder.dispatch()


def _resolution_target_ids(resolutions: list[Resolution]) -> set[str]:
    """Union of hypothesis IDs the Recorder will read or write against.

    A `corroborates` resolution targets its corroborated ID plus any
    contradicted IDs. A `contributes` resolution targets its contradicted
    IDs only — the novel itself is created inside the transaction and has
    no prior state to fetch.
    """
    target_ids: set[str] = set()
    for resolution in resolutions:
        if resolution.corroborates is not None:
            target_ids.add(resolution.corroborates)
        target_ids.update(resolution.contradicts)
    return target_ids


def _to_evidence(records: list[AttestationRecord]) -> list[EvidenceInput]:
    """Map attestation records to math service inputs."""
    return [
        EvidenceInput(c_oracle_discounted=a.c_oracle_discounted, timestamp=a.timestamp)
        for a in records
    ]


def _count_distinct_oracles(*, records: list[AttestationRecord], exclude: str) -> int:
    """Count distinct oracle IDs, excluding the current oracle."""
    return len({a.oracle_id for a in records} - {exclude})


def _latest_row(records: list[AttestationRecord]) -> AttestationRecord | None:
    """Latest attestation row by ``(timestamp, id)`` lexicographic ordering.

    Matches the ``ORDER BY timestamp, id`` in ``find_by_hypotheses`` so
    transfer-attestation reads see the same row the trust scan sees. UUIDv4
    ids tiebreak deterministically but not causally — readers must not
    infer event ordering from the tiebreak when timestamps collide.
    """
    if not records:
        return None
    return max(records, key=lambda a: (a.timestamp, a.id))


class Recorder:
    """Transaction-scoped coordinator that dispatches an ``ArchivistOutput``
    onto the ledger.

    ``attestation_map`` must be a transaction-scoped snapshot of the union of
    corroborated and contradicted IDs; the Recorder reads it but never mutates
    it.

    The single validated precondition the Recorder relies on is
    ``ArchivistOutput._disjoint_resolution_ids``: across all resolutions, every
    hypothesis ID appears in at most one ``corroborates`` or ``contradicts``
    slot. That invariant is what makes the per-resolution dispatch safe — the
    oracle attests on each existing hypothesis at most once per consult, so the
    snapshot-uniqueness guarantee on ``c_herd`` holds.
    """

    def __init__(
        self,
        *,
        hypotheses: HypothesisRepository,
        attestations: AttestationsRepository,
        math: MathService,
        reasoned: ArchivistOutput,
        attestation_map: dict[str, list[AttestationRecord]],
        novel_embeddings: dict[str, list[float]],
        oracle_id: str,
        correlation_id: str,
        confidence: float,
        t_oracle: float,
        t_now: int,
        settings: LoreSettings,
    ) -> None:
        # _transfer is reserved for _compute_transfer (full credibility, no
        # discount). Accepting it as the principal oracle_id would bypass
        # trust discounting at the one site it lives. The adapter rejects
        # IdP-claimed _* values; this is the domain-layer enforcement.
        if oracle_id == TRANSFER_ORACLE:
            msg = (
                f"Recorder.oracle_id must not equal {TRANSFER_ORACLE!r} —"
                " that synthetic is reserved for _compute_transfer"
            )
            raise ValueError(msg)
        self._hypotheses = hypotheses
        self._attestations = attestations
        self._math = math
        self._reasoned = reasoned
        self._attestation_map = attestation_map
        self._novel_embeddings = novel_embeddings
        self._oracle_id = oracle_id
        self._correlation_id = correlation_id
        self._confidence = confidence
        self._t_oracle = t_oracle
        self._t_now = t_now
        self._settings = settings

    async def dispatch(self) -> None:
        """Write attestations for every resolution in ``self._reasoned``.

        Iterates the validated ``ArchivistOutput`` in order; each resolution
        sets exactly one of ``corroborates`` or ``contributes`` (enforced by
        ``Resolution._validate_shape``).
        """
        for resolution in self._reasoned.resolutions:
            if (corroborates := resolution.corroborates) is not None:
                await self._corroborate(corroborates, contradicts=resolution.contradicts)
            elif (contributes := resolution.contributes) is not None:
                await self._contribute(contributes, contradicts=resolution.contradicts)

    async def _corroborate(self, corroborates: str, *, contradicts: list[str]) -> None:
        """Positive attestation on the paraphrased hypothesis; negatives on contradicted."""
        log.info("resolution.paraphrase", corroborates=corroborates, contradicts=contradicts)
        await self._attest_existing(hypothesis_id=corroborates, confidence=self._confidence)
        for h_id in contradicts:
            await self._attest_existing(hypothesis_id=h_id, confidence=-self._confidence)

    async def _contribute(self, contributes: str, *, contradicts: list[str]) -> None:
        """Store novel; consolidated transfer (against pre-contradiction state);
        oracle attestation (fuses against transfer); negatives on contradicted.
        Ordering matters."""
        log.info(
            "resolution.contribute",
            contributes_length=len(contributes),
            contributes_sha256=hashlib.sha256(contributes.encode()).hexdigest()[:16],
            contradicts=contradicts,
        )

        transfer = self._compute_transfer(contradicts)

        record = await self._hypotheses.store(
            content=contributes,
            embedding=self._novel_embeddings[contributes],
            created_at=self._t_now,
        )

        transfer_evidence: list[EvidenceInput] = []
        if transfer is not None:
            log.debug(
                "recorder.attestation",
                hypothesis_id=record.id,
                oracle_id=TRANSFER_ORACLE,
                c_oracle_raw=transfer,
                c_oracle_discounted=transfer,
                c_herd=transfer,
                t_oracle=1.0,
                n_oracle_prior=0,
            )
            await self._attestations.append(
                hypothesis_id=record.id,
                oracle_id=TRANSFER_ORACLE,
                correlation_id=self._correlation_id,
                timestamp=self._t_now,
                t_oracle=1.0,
                c_oracle_raw=transfer,
                c_oracle_discounted=transfer,
                c_herd=transfer,
                n_oracle_prior=0,
            )
            transfer_evidence.append(
                EvidenceInput(c_oracle_discounted=transfer, timestamp=self._t_now)
            )

        computed = self._math.prepare_attestation(
            confidence=self._confidence,
            existing=transfer_evidence,
            t_now=self._t_now,
            t_oracle=self._t_oracle,
            n_oracle_prior=0,
        )
        await self._record_attestation(hypothesis_id=record.id, computed=computed, n_oracle_prior=0)

        for h_id in contradicts:
            await self._attest_existing(hypothesis_id=h_id, confidence=-self._confidence)

    def _compute_transfer(self, contradicts: list[str]) -> float | None:
        """Consolidated transfer scalar from the latest c_herd per contradicted
        hypothesis, fused via decayed ECBF and negated. Returns None when the
        fused magnitude falls below ``settings.trust.threshold`` (e.g.
        balanced contradictions): no transfer row is written."""
        evidence_pieces: list[EvidenceInput] = []
        for h_id in contradicts:
            latest = _latest_row(self._attestation_map.get(h_id, []))
            if latest is None:
                continue
            evidence_pieces.append(
                EvidenceInput(c_oracle_discounted=latest.c_herd, timestamp=latest.timestamp)
            )
        if not evidence_pieces:
            return None
        fused = self._math.compute_confidence(attestations=evidence_pieces, t_now=self._t_now)
        c_transfer = -fused
        if abs(c_transfer) < self._settings.trust.threshold:
            log.debug(
                "recorder.transfer_skipped",
                contradicts=contradicts,
                c_transfer=c_transfer,
            )
            return None
        return c_transfer

    async def _attest_existing(self, *, hypothesis_id: str, confidence: float) -> None:
        """Write an attestation (positive or negative) on an existing hypothesis."""
        existing = self._attestation_map.get(hypothesis_id, [])
        # ``n_oracle_prior`` is stored on the row at write time and read back
        # unchanged by the trust scan — the column is the single source of
        # truth. The semantics ("distinct oracles other than self") live here;
        # changing this helper changes the meaning for *new* rows only, so any
        # change must consider that historical rows preserve the old semantics.
        n_oracle_prior = _count_distinct_oracles(records=existing, exclude=self._oracle_id)
        computed = self._math.prepare_attestation(
            confidence=confidence,
            existing=_to_evidence(existing),
            t_now=self._t_now,
            t_oracle=self._t_oracle,
            n_oracle_prior=n_oracle_prior,
        )
        await self._record_attestation(
            hypothesis_id=hypothesis_id, computed=computed, n_oracle_prior=n_oracle_prior
        )

    async def _record_attestation(
        self,
        *,
        hypothesis_id: str,
        computed: AttestationComputed,
        n_oracle_prior: int,
    ) -> None:
        """Log + append for the oracle-attestation pair. The transfer path stays
        inline above — its values are pinned, not computed."""
        log.debug(
            "recorder.attestation",
            hypothesis_id=hypothesis_id,
            oracle_id=self._oracle_id,
            c_oracle_raw=computed.c_oracle_raw,
            c_oracle_discounted=computed.c_oracle_discounted,
            c_herd=computed.c_herd,
            t_oracle=computed.t_oracle,
            n_oracle_prior=n_oracle_prior,
        )
        await self._attestations.append(
            hypothesis_id=hypothesis_id,
            oracle_id=self._oracle_id,
            correlation_id=self._correlation_id,
            timestamp=self._t_now,
            t_oracle=computed.t_oracle,
            c_oracle_raw=computed.c_oracle_raw,
            c_oracle_discounted=computed.c_oracle_discounted,
            c_herd=computed.c_herd,
            n_oracle_prior=n_oracle_prior,
        )
