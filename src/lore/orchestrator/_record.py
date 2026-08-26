"""Record stage: transaction-scoped writes."""

import hashlib
from typing import TYPE_CHECKING

import structlog

from lore.domain import (
    TRANSFER_ORACLE,
    ArchivistOutput,
    AttestationComputed,
    EvidenceInput,
    Resolution,
    WriteContext,
)
from lore.math import MathService
from lore.repositories import AttestationRecord, DecayWindow, LedgerView, Repositories, generate_id
from lore.telemetry import start_span

if TYPE_CHECKING:
    from lore.config import LoreSettings

log = structlog.get_logger(__name__)


async def record(
    *,
    repos: Repositories,
    math: MathService,
    reasoned: ArchivistOutput,
    novel_embeddings: dict[str, list[float]],
    context: WriteContext,
    settings: LoreSettings,
) -> None:
    """Trust scan, attestation refetch, and writes, all inside the caller's
    ``pool.transaction()``. The trust pipeline (maturity, ECBF) needs a
    snapshot-consistent attestation map; the read-path ``enrich`` snapshot
    feeds only the Archivist."""
    target_ids = _resolution_target_ids(reasoned.resolutions)
    with start_span("lore.record"):
        alignments = await repos.attestations.fetch_trust_alignments(
            oracle_id=context.oracle_id,
            t_now=context.t_now,
            trust_half_life=settings.epistemics.trust_half_life,
        )
        # Others-only evidence per scan hypothesis: the witness rule's
        # reference material. Sorted ids keep the IN-list deterministic.
        evidence_window = DecayWindow(
            t_now=context.t_now, half_life=settings.epistemics.attestation_half_life
        )
        herd_evidence = (
            await repos.attestations.fetch_herd_evidence(
                sorted({a.hypothesis_id for a in alignments}),
                exclude_oracle=context.oracle_id,
                window=evidence_window,
            )
            if alignments
            else {}
        )
        t_oracle = math.compute_oracle_trust(
            rows=alignments, herd_evidence=herd_evidence, t_now=context.t_now
        )

        # No decay window here: maturity counts distinct oracles over full
        # history and the transfer needs the true latest row.
        attestation_map: dict[str, LedgerView] = (
            await repos.attestations.find_by_hypotheses(list(target_ids), window=None)
            if target_ids
            else {}
        )

        recorder = Recorder(
            repos=repos,
            math=math,
            reasoned=reasoned,
            attestation_map=attestation_map,
            novel_embeddings=novel_embeddings,
            context=context,
            t_oracle=t_oracle,
            settings=settings,
        )

        await recorder.dispatch()


def _resolution_target_ids(resolutions: list[Resolution]) -> set[str]:
    """Union of hypothesis IDs the Recorder will read or write against.

    A `corroborates` resolution targets its corroborated ID plus any
    contradicted IDs. A `contributes` resolution targets its contradicted
    IDs only: the novel itself is created inside the transaction and has
    no prior state to fetch.
    """
    target_ids: set[str] = set()
    for resolution in resolutions:
        if resolution.corroborates is not None:
            target_ids.add(resolution.corroborates)
        target_ids.update(resolution.contradicts)
    return target_ids


def _to_evidence(records: list[AttestationRecord]) -> list[EvidenceInput]:
    return [
        EvidenceInput(c_oracle_discounted=a.c_oracle_discounted, timestamp=a.timestamp)
        for a in records
    ]


def _count_distinct_oracles(*, records: list[AttestationRecord], exclude: str) -> int:
    """Distinct oracles who took a stance, ``exclude`` removed.

    Vacuous rows do not count. Maturity gates how hard one voice may push,
    and what makes that safe on a scrutinized hypothesis is accumulated
    evidence to compound against; ECBF over vacuous opinions is vacuous, so
    counting shrugs would raise M while the counterweight stays zero. A 0.0
    row already earns its author nothing (conviction 0 drops it from its own
    trust aggregate); it buys the next writer nothing either.

    The test is exact, not tolerance-based: ``confidence = 0.0`` is the
    documented vacuous state at the interface, and a stated 1e-9 is a
    commitment, however small.
    """
    return len({a.oracle_id for a in records if a.c_oracle_raw != 0.0} - {exclude})


def _latest_row(records: list[AttestationRecord]) -> AttestationRecord | None:
    """Latest attestation row by ``(timestamp, id)`` lexicographic ordering.

    Matches the ``ORDER BY timestamp, id`` in ``find_by_hypotheses`` so
    transfer-attestation reads see the same row the trust scan sees. UUIDv4
    ids tiebreak deterministically but not causally; readers must not
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
    slot. That invariant is what makes the per-resolution dispatch safe: the
    oracle attests on each existing hypothesis at most once per consult, so the
    snapshot-uniqueness guarantee on ``c_herd`` holds.
    """

    def __init__(
        self,
        *,
        repos: Repositories,
        math: MathService,
        reasoned: ArchivistOutput,
        attestation_map: dict[str, LedgerView],
        novel_embeddings: dict[str, list[float]],
        context: WriteContext,
        t_oracle: float,
        settings: LoreSettings,
    ) -> None:
        # _transfer is reserved for _compute_transfer (full credibility, no
        # discount). Accepting it as the principal oracle_id would bypass
        # trust discounting at the one site it lives. The adapter's identity
        # middleware rejects empty and _* subs at the boundary; this guard
        # stays as defense-in-depth for the reserved synthetic.
        if context.oracle_id == TRANSFER_ORACLE:
            msg = (
                f"Recorder.context.oracle_id must not equal {TRANSFER_ORACLE!r}:"
                " that synthetic is reserved for _compute_transfer"
            )
            raise ValueError(msg)
        self._repos = repos
        self._math = math
        self._reasoned = reasoned
        self._attestation_map = attestation_map
        self._novel_embeddings = novel_embeddings
        self._context = context
        self._t_oracle = t_oracle
        self._settings = settings

    async def dispatch(self) -> None:
        """Each resolution sets exactly one of ``corroborates`` or
        ``contributes`` (``Resolution._validate_shape``), so the branch below
        is exhaustive."""
        for resolution in self._reasoned.resolutions:
            if (corroborates := resolution.corroborates) is not None:
                await self._corroborate(corroborates, contradicts=resolution.contradicts)
            elif (contributes := resolution.contributes) is not None:
                await self._contribute(contributes, contradicts=resolution.contradicts)

    async def _corroborate(self, corroborates: str, *, contradicts: list[str]) -> None:
        log.info("resolution.paraphrase", corroborates=corroborates, contradicts=contradicts)
        await self._attest_existing(hypothesis_id=corroborates, confidence=self._context.confidence)
        for h_id in contradicts:
            await self._attest_existing(hypothesis_id=h_id, confidence=-self._context.confidence)

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

        record = await self._repos.hypotheses.store(
            content=contributes,
            embedding=self._novel_embeddings[contributes],
            created_at=self._context.t_now,
        )

        transfer_rows: list[AttestationRecord] = []
        if transfer is not None:
            # The transfer row is the novel's first row, so nothing precedes
            # it: its own n_oracle_prior is 0.
            transfer_row = AttestationRecord(
                id=generate_id(),
                hypothesis_id=record.id,
                oracle_id=TRANSFER_ORACLE,
                correlation_id=self._context.correlation_id,
                timestamp=self._context.t_now,
                t_oracle=1.0,
                c_oracle_raw=transfer,
                c_oracle_discounted=transfer,
                c_herd=transfer,
                n_oracle_prior=0,
            )
            log.debug(
                "recorder.attestation",
                hypothesis_id=transfer_row.hypothesis_id,
                oracle_id=transfer_row.oracle_id,
                c_oracle_raw=transfer_row.c_oracle_raw,
                c_oracle_discounted=transfer_row.c_oracle_discounted,
                c_herd=transfer_row.c_herd,
                t_oracle=transfer_row.t_oracle,
                n_oracle_prior=transfer_row.n_oracle_prior,
            )
            await self._repos.attestations.append(transfer_row)
            transfer_rows.append(transfer_row)

        # The transfer row is prior scrutiny on the novel, and the oracle's
        # own row fuses against it. Counting it through the same helper the
        # existing path uses keeps one definition of what counts, so the
        # row's maturity and its c_herd_prior describe the same room.
        n_oracle_prior = _count_distinct_oracles(
            records=transfer_rows, exclude=self._context.oracle_id
        )
        computed = self._math.prepare_attestation(
            confidence=self._context.confidence,
            existing=_to_evidence(transfer_rows),
            t_now=self._context.t_now,
            t_oracle=self._t_oracle,
            n_oracle_prior=n_oracle_prior,
        )
        await self._record_attestation(
            hypothesis_id=record.id, computed=computed, n_oracle_prior=n_oracle_prior
        )

        for h_id in contradicts:
            await self._attest_existing(hypothesis_id=h_id, confidence=-self._context.confidence)

    def _compute_transfer(self, contradicts: list[str]) -> float | None:
        """Consolidated transfer scalar from the latest c_herd per contradicted
        hypothesis, fused via decayed ECBF and negated. Returns None when the
        fused magnitude falls below ``settings.epistemics.transfer_threshold`` (e.g.
        balanced contradictions): no transfer row is written."""
        evidence_pieces: list[EvidenceInput] = []
        for h_id in contradicts:
            # Indexed, not ``.get``: find_by_hypotheses keys every requested
            # ID, and these were requested. A missing key is a broken
            # repository contract and must raise, not silently drop the
            # hypothesis from the transfer. An empty view is different: a
            # hypothesis with no rows in range carries nothing to transfer.
            latest = _latest_row(self._attestation_map[h_id].rows)
            if latest is None:
                continue
            evidence_pieces.append(
                EvidenceInput(c_oracle_discounted=latest.c_herd, timestamp=latest.timestamp)
            )
        if not evidence_pieces:
            return None
        fused = self._math.compute_confidence(
            attestations=evidence_pieces, t_now=self._context.t_now
        )
        c_transfer = -fused
        if abs(c_transfer) < self._settings.epistemics.transfer_threshold:
            log.debug(
                "recorder.transfer_skipped",
                contradicts=contradicts,
                c_transfer=c_transfer,
            )
            return None
        return c_transfer

    async def _attest_existing(self, *, hypothesis_id: str, confidence: float) -> None:
        # Indexed for the same reason as the transfer above: a missing key
        # would otherwise read as a fresh hypothesis and write the row
        # against a vacuous prior, masking the contract violation.
        existing = self._attestation_map[hypothesis_id].rows
        # ``n_oracle_prior`` is stored on the row at write time and read back
        # unchanged by the trust scan: the column is the single source of
        # truth. The semantics ("distinct oracles other than self") live here;
        # changing this helper changes the meaning for *new* rows only, so any
        # change must consider that historical rows preserve the old semantics.
        n_oracle_prior = _count_distinct_oracles(records=existing, exclude=self._context.oracle_id)
        computed = self._math.prepare_attestation(
            confidence=confidence,
            existing=_to_evidence(existing),
            t_now=self._context.t_now,
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
        """The transfer path in ``_contribute`` stays inline (its values are
        pinned, not computed) rather than routing through this helper."""
        log.debug(
            "recorder.attestation",
            hypothesis_id=hypothesis_id,
            oracle_id=self._context.oracle_id,
            c_oracle_raw=computed.c_oracle_raw,
            c_oracle_discounted=computed.c_oracle_discounted,
            c_herd=computed.c_herd,
            t_oracle=computed.t_oracle,
            n_oracle_prior=n_oracle_prior,
        )
        await self._repos.attestations.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=hypothesis_id,
                oracle_id=self._context.oracle_id,
                correlation_id=self._context.correlation_id,
                timestamp=self._context.t_now,
                t_oracle=computed.t_oracle,
                c_oracle_raw=computed.c_oracle_raw,
                c_oracle_discounted=computed.c_oracle_discounted,
                c_herd=computed.c_herd,
                n_oracle_prior=n_oracle_prior,
            )
        )
