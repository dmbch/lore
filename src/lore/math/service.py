"""Math service — orchestrator-facing API for the math engine.

Accepts scalar confidences and timestamps. Returns computed epistemic fields.
Opinion never crosses this boundary — it is an implementation detail of the
algebra.

MathService wraps all orchestrator-facing math: hypothesis-level operations
(prepare_attestation, compute_confidence) and oracle trust computation
(compute_oracle_trust).
"""

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

from lore.domain import AttestationComputed, EvidenceInput, TrustSignal
from lore.math.confidence import to_confidence, to_opinion
from lore.math.conflict import compute_projected_distance
from lore.math.discount import discount
from lore.math.hypothesis import OpinionAtTime, compute_hypothesis_state
from lore.math.maturity import compute_maturity
from lore.math.opinion import BASE_RATE

if TYPE_CHECKING:
    from lore.config import LoreSettings


class MathService:
    """Orchestrator-facing wrapper around the math engine.

    Takes attestation decay config (c_half_life, maturity_k) and trust decay
    config (t_half_life) at construction. Converts to internal rate constants.
    Methods accept scalar confidences and timestamps — Opinion never leaks out.

    c_half_life=float("inf") is valid and produces λ=0 (no decay). This is a
    legitimate deployment mode for archives where knowledge never expires.
    t_half_life=float("inf") is valid symmetrically — oracle trust scans
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
        maturity_k: float = 1.0,
        t_half_life: float,
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

    def compute_oracle_trust(
        self,
        *,
        rows: Sequence[TrustSignal],
        t_now: int,
    ) -> float:
        """Compute oracle trust as a conviction-weighted, decay-weighted alignment average.

        The orchestrator fetches alignment rows from the repository
        as TrustSignal and passes them here.

        Each row's contribution combines four factors:

        - **Adaptive write/read blend.** Per-row maturity M_write (from
          n_oracle_prior) controls how much the write-time signal counts.
          Fresh hypotheses (M_write ≈ 0.5) give read-time the final say;
          mature hypotheses (M_write → 1) anchor on write-time alignment.
        - **Info weighting (Def. 14.6).** The row's raw alignment is
          discounted toward the base rate (0.5) in proportion to the herd's
          prior certainty. Agreement with a settled herd resolves no
          uncertainty and earns little trust credit.
        - **Conviction.** |c_oracle_raw| — vacuous attestations contribute
          nothing. Saying nothing is not evidence of alignment.
        - **Temporal decay.** Exponential over timestamp age.

        Returns t_oracle in [0, 1]. Empty rows or histories with no
        informative contribution (all-vacuous or all-bandwagon) return 0.5.
        See docs/logic.md, Oracle Trust section.

        Per-row alignment is ``1 - compute_projected_distance(...)`` over
        the uncertainty-maximized opinions for the two scalars (Eq. 4.61
        specialised at base rate 0.5). Info weighting applies the discount
        operator (Def. 14.6) to the scalar alignment signal.
        """
        if not rows:
            return BASE_RATE

        def _row_contributions(row: TrustSignal) -> tuple[float, float]:
            m_write = compute_maturity(n_oracle_prior=row.n_oracle_prior, k=self._maturity_k)
            oracle_opinion = to_opinion(row.c_oracle_raw)
            align_write = 1.0 - compute_projected_distance(
                oracle_opinion, to_opinion(row.c_herd_prior)
            )
            align_read = 1.0 - compute_projected_distance(
                oracle_opinion, to_opinion(row.c_herd_now)
            )
            align = m_write * align_write + (1.0 - m_write) * align_read

            info = 1.0 - abs(row.c_herd_prior)
            effective_align = info * align + (1.0 - info) * BASE_RATE

            conviction = abs(row.c_oracle_raw)
            dt = max(0, t_now - row.timestamp)
            weight = math.exp(-self._trust_lambda * dt)

            den = conviction * weight
            return effective_align * den, den

        # math.fsum is Shewchuk-exact and order-independent — t_oracle stays
        # bit-stable across row reorderings even when contributions mix
        # magnitudes that would drift under naive ``+=``.
        contributions = [_row_contributions(row) for row in rows]
        numerator = math.fsum(num for num, _ in contributions)
        denominator = math.fsum(den for _, den in contributions)

        # Denominator is zero iff every row has conviction == 0 (all-vacuous
        # history), since weight > 0 always. Informationally identical to
        # cold start → base rate trust.
        if denominator == 0.0:
            return BASE_RATE

        # Clamp IEEE 754 noise to [0, 1] — same defensive pattern as decay and
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
    """Convert evidence inputs to OpinionAtTime pairs."""
    return [OpinionAtTime(to_opinion(a.c_oracle_discounted), a.timestamp) for a in attestations]
