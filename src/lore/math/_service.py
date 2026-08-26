"""Math service: orchestrator-facing API for the math engine.

Accepts scalar confidences and timestamps. Returns computed epistemic fields.
Opinion never crosses this boundary: it is an implementation detail of the
algebra.

MathService wraps all orchestrator-facing math: hypothesis-level operations
(prepare_attestation, compute_confidence) and oracle trust computation
(compute_oracle_trust).
"""

import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from lore.domain import AttestationComputed, EvidenceInput, TrustSignal
from lore.math._confidence import to_confidence, to_opinion, to_uncertainty
from lore.math._conflict import compute_projected_distance
from lore.math._discount import discount
from lore.math._hypothesis import OpinionAtTime, compute_hypothesis_state
from lore.math._maturity import compute_maturity
from lore.math._opinion import BASE_RATE

if TYPE_CHECKING:
    from lore.config import LoreSettings


class MathService:
    """Orchestrator-facing wrapper around the math engine.

    Takes attestation decay config (c_half_life, maturity_k) and trust decay
    config (t_half_life) at construction. Converts to internal rate constants.
    Methods accept scalar confidences and timestamps; Opinion never leaks out.

    c_half_life=float("inf") is valid and produces λ=0 (no decay). This is a
    legitimate deployment mode for archives where knowledge never expires.
    t_half_life=float("inf") is valid symmetrically; oracle trust scans
    every prior row with uniform weight.

    The oracle trust computation uses an adaptive blend between write-time and
    read-time alignment signals derived from per-row hypothesis maturity
    (M_write). The global alignment weight `w` has been replaced by this
    adaptive blend; see docs/logic.md, Oracle Trust section.
    """

    def __init__(
        self,
        *,
        c_half_life: float,
        t_half_life: float,
        maturity_k: float = 1.0,
    ) -> None:
        self._lambda = math.log(2) / c_half_life
        self._maturity_k = maturity_k
        self._trust_lambda = math.log(2) / t_half_life

    def prepare_attestation(
        self,
        *,
        confidence: float,
        existing: Sequence[EvidenceInput],
        t_now: int,
        t_oracle: float,
        n_oracle_prior: int,
    ) -> AttestationComputed:
        """Compose maturity → P_effective → discount → ECBF; return the four
        ledger-bound fields for a new attestation.
        """
        if not 0.0 <= t_oracle <= 1.0:
            msg = f"t_oracle must be in [0, 1], got {t_oracle}"
            raise ValueError(msg)
        if n_oracle_prior < 0:
            msg = f"n_oracle_prior must be non-negative, got {n_oracle_prior}"
            raise ValueError(msg)
        maturity = compute_maturity(n_oracle_prior=n_oracle_prior, k=self._maturity_k)
        p_effective = maturity * t_oracle
        c_oracle_discounted = discount(confidence=confidence, p_effective=p_effective)

        discounted_opinion = to_opinion(c_oracle_discounted)
        existing_pairs = _to_opinion_at_times(existing)

        all_pairs: list[OpinionAtTime] = [
            *existing_pairs,
            OpinionAtTime(discounted_opinion, t_now),
        ]
        herd_posterior = compute_hypothesis_state(
            attestations=all_pairs, lambda_=self._lambda, t_now=t_now
        )

        return AttestationComputed(
            t_oracle=t_oracle,
            c_oracle_raw=confidence,
            c_oracle_discounted=c_oracle_discounted,
            c_herd=to_confidence(herd_posterior),
        )

    def compute_confidence(
        self,
        *,
        attestations: Sequence[EvidenceInput],
        t_now: int,
    ) -> float:
        """Compute the current confidence scalar for a hypothesis.

        Converts attestation scalars to internal opinions, applies per-attestation
        decay, fuses with ECBF, and projects back to a scalar.

        Returns 0.0 for an empty attestation list (vacuous = ignorance).
        """
        pairs = _to_opinion_at_times(attestations)
        state = compute_hypothesis_state(attestations=pairs, lambda_=self._lambda, t_now=t_now)
        return to_confidence(state)

    def compute_uncertainty(self, confidence: float) -> float:
        """Project a confidence scalar to its opinion's uncertainty (u = 1 − |c|)."""
        return to_uncertainty(confidence)

    def compute_oracle_trust(
        self,
        *,
        rows: Sequence[TrustSignal],
        herd_evidence: Mapping[str, Sequence[EvidenceInput]],
        t_now: int,
    ) -> float:
        """Compute oracle trust as a conviction-weighted, decay-weighted alignment average.

        The orchestrator fetches alignment rows and per-hypothesis
        others-only evidence from the repository and passes both here.
        ``herd_evidence`` must carry a key for every hypothesis in
        ``rows`` (the repository's all-keys-present contract); a missing
        key is a caller bug and raises KeyError.

        Each row's contribution combines five factors:

        - **Witness rule.** A row counts only if other oracles (the
          synthetic ``_transfer`` included) left evidence on its
          hypothesis inside the decay window. Unwitnessed rows leave the
          scan entirely: no numerator, no denominator. Solo novels earn
          nothing; agreement with yourself is not alignment.
        - **Adaptive write/read blend.** Per-row maturity M_write (from
          n_oracle_prior) controls how much the write-time signal counts.
          Fresh hypotheses (M_write ≈ 0.5) give read-time the final say;
          mature hypotheses (M_write → 1) anchor on write-time alignment.
          The read-time reference is the others-only herd state recomputed
          at ``t_now`` from ``herd_evidence`` (decay + ECBF), never a
          stored snapshot: the oracle's own rows cannot sit inside it.
        - **Informative-commitment gate (Def. 14.6).** The composite
          ``signal = conviction * info`` discounts the row's alignment
          toward the base rate (0.5): one discount, two conditions.
          Agreement with a settled herd resolves no uncertainty; a
          hedged opinion asserts almost nothing. Neither earns trust
          credit far from base rate.
        - **Conviction row weight.** |c_oracle_raw| also weights the row:
          vacuous attestations contribute nothing. A weight normalizes
          out over uniform histories, a calibration cannot, so the two
          conviction roles are deliberate, not double-counting.
        - **Temporal decay.** Exponential over timestamp age.

        Returns t_oracle in [0, 1]. Empty rows or histories with no
        countable contribution (all-vacuous, all-bandwagon, or
        all-unwitnessed) return 0.5. See docs/logic.md, Oracle Trust
        section.

        Per-row alignment is ``1 - compute_projected_distance(...)`` over
        the uncertainty-maximized opinions for the two scalars (Eq. 4.61
        specialised at base rate 0.5). The informative-commitment gate
        applies the discount operator (Def. 14.6) to the scalar alignment
        signal; two discounts toward the same base rate compose into the
        single product ``conviction * info``.
        """
        if not rows:
            return BASE_RATE

        # One reference per witnessed hypothesis, recomputed at t_now.
        # Presence in the map is the witness test: compute_confidence([])
        # would return 0.0, indistinguishable from a genuine zero state.
        references = {
            hid: self.compute_confidence(attestations=evidence, t_now=t_now)
            for hid in {row.hypothesis_id for row in rows}
            if (evidence := herd_evidence[hid])
        }

        def _row_contributions(row: TrustSignal) -> tuple[float, float]:
            reference = references.get(row.hypothesis_id)
            if reference is None:
                return 0.0, 0.0
            m_write = compute_maturity(n_oracle_prior=row.n_oracle_prior, k=self._maturity_k)
            oracle_opinion = to_opinion(row.c_oracle_raw)
            align_write = 1.0 - compute_projected_distance(
                oracle_opinion, to_opinion(row.c_herd_prior)
            )
            align_read = 1.0 - compute_projected_distance(oracle_opinion, to_opinion(reference))
            align = m_write * align_write + (1.0 - m_write) * align_read

            info = 1.0 - abs(row.c_herd_prior)
            conviction = abs(row.c_oracle_raw)
            signal = conviction * info
            effective_align = signal * align + (1.0 - signal) * BASE_RATE

            dt = max(0, t_now - row.timestamp)
            weight = math.exp(-self._trust_lambda * dt)

            den = conviction * weight
            return effective_align * den, den

        # math.fsum is Shewchuk-exact and order-independent, so t_oracle stays
        # bit-stable across row reorderings even when contributions mix
        # magnitudes that would drift under naive ``+=``.
        contributions = [_row_contributions(row) for row in rows]
        numerator = math.fsum(num for num, _ in contributions)
        denominator = math.fsum(den for _, den in contributions)

        # Denominator is zero iff every countable row has conviction == 0
        # (weight > 0 always; unwitnessed rows contribute zero to both sums).
        # All-vacuous and all-unwitnessed alike are informationally identical
        # to cold start → base rate trust.
        if denominator == 0.0:
            return BASE_RATE

        # Clamp IEEE 754 noise to [0, 1], same defensive pattern as decay and
        # maximize_uncertainty. The algebra guarantees the range but division
        # can produce values like 1.0000000000000002 from accumulated rounding.
        return max(0.0, min(1.0, numerator / denominator))


def build_math(settings: LoreSettings) -> MathService:
    return MathService(
        c_half_life=settings.epistemics.attestation_half_life,
        t_half_life=settings.epistemics.trust_half_life,
        maturity_k=settings.epistemics.maturity_k,
    )


def _to_opinion_at_times(
    attestations: Sequence[EvidenceInput],
) -> list[OpinionAtTime]:
    return [OpinionAtTime(to_opinion(a.c_oracle_discounted), a.timestamp) for a in attestations]
