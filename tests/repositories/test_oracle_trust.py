"""Integration tests for oracle trust: SQL fetch + math computation.

Seeds attestation data, fetches alignment rows via the repository, and
passes them through MathService.compute_oracle_trust to verify the full
pipeline.

The SQL layer derives c_herd_prior (LAG) and c_herd_now (FIRST_VALUE DESC)
via window functions. The math service computes alignment and decay weighting.
Both are exercised together here; the method is tested in isolation
in tests/math/test_service.py.

See docs/logic.md, Oracle Trust section.
"""

import math

import pytest

from lore.math.service import MathService
from lore.repositories import AttestationRecord
from lore.repositories.protocols import (
    AttestationsRepository,
    HypothesisRepository,
    RequestRepository,
)
from lore.repositories.records import generate_id
from tests.repositories.conftest import (
    EPSILON,
    seed_hypothesis,
    seed_request,
)
from tests.repositories.conftest import NO_DECAY_TRUST_HL as _NO_DECAY_HL

# Attestation decay is irrelevant for trust tests — use no-decay.
_TRUST_SVC = MathService(c_half_life=1e12, t_half_life=_NO_DECAY_HL)

# The default correlation_id on append_attestation — every trust test uses
# the helper, so we pre-seed its parent request row per test via autouse.
# Visible here at module top instead of buried in a shared fixture.
_DEFAULT_CORRELATION_ID = "00000000-0000-0000-0000-000000000099"


@pytest.fixture(autouse=True)
async def seed_default_request(request_repo: RequestRepository) -> None:
    """Parent request row for the default ``append_attestation`` correlation_id."""
    await seed_request(request_repo, correlation_id=_DEFAULT_CORRELATION_ID)


class TestFetchTrustAlignments:
    async def test_no_history_returns_empty(
        self,
        attestations_repo: AttestationsRepository,
    ) -> None:
        """Cold start: oracle with no attestation history returns empty rows."""
        rows = await attestations_repo.fetch_trust_alignments(
            oracle_id="sub:unknown",
            t_now=5000,
            trust_half_life=_NO_DECAY_HL,
        )
        assert rows == []
        # Math layer: empty → base rate trust 0.5
        result = _TRUST_SVC.compute_oracle_trust(rows=rows, t_now=5000)
        assert abs(result - 0.5) < EPSILON

    async def test_perfect_alignment_single_hypothesis(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        """Oracle whose opinions match the herd exactly.

        Setup: Oracle B seeds H1, then Oracle A attests with identical values.
        c_herd_prior = 0.5 (B's c_herd), c_herd_now = 0.5 (A's is latest).

        n_oracle_prior = 1 (B), N_O = 2, M_write = 2/3.
        align_write = align_read = 1.0 → align = 1.0.
        info = 1 - 0.5 = 0.5 → effective_align = 0.5*1.0 + 0.5*0.5 = 0.75.
        Single row → t_oracle = 0.75.
        """
        h_id = await seed_hypothesis(hypothesis_repo)

        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-B",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=100,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.5,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=200,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.5,
                n_oracle_prior=1,
            )
        )

        rows = await attestations_repo.fetch_trust_alignments(
            oracle_id="sub:oracle-A",
            t_now=200,
            trust_half_life=_NO_DECAY_HL,
        )
        assert len(rows) == 1
        assert abs(rows[0].c_oracle_raw - 0.5) < EPSILON
        assert abs(rows[0].c_herd_prior - 0.5) < EPSILON
        assert abs(rows[0].c_herd_now - 0.5) < EPSILON

        result = _TRUST_SVC.compute_oracle_trust(rows=rows, t_now=200)
        assert abs(result - 0.75) < EPSILON

    async def test_first_attestation_c_herd_prior_defaults_to_zero(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        """First attester on a hypothesis: c_herd_prior = 0.0.

        Setup: Oracle A is the sole attester on H1.
        c_herd_prior = 0.0 (no preceding attestation)
        c_herd_now   = 0.6 (A's own c_herd is the latest)

        n_oracle_prior = 0, N_O = 1, M_write = 0.5.
        align_write = 1 - 0.5*0.6 = 0.7.
        align_read  = 1 - 0.5*0.0 = 1.0.
        align       = 0.5*0.7 + 0.5*1.0 = 0.85.
        info        = 1 - 0 = 1.0 → effective_align = 0.85.
        Single row → t_oracle = 0.85.
        """
        h_id = await seed_hypothesis(hypothesis_repo)

        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=1000,
                t_oracle=0.5,
                c_oracle_raw=0.6,
                c_oracle_discounted=0.3,
                c_herd=0.6,
                n_oracle_prior=0,
            )
        )

        rows = await attestations_repo.fetch_trust_alignments(
            oracle_id="sub:oracle-A",
            t_now=1000,
            trust_half_life=_NO_DECAY_HL,
        )
        assert len(rows) == 1
        assert abs(rows[0].c_herd_prior - 0.0) < EPSILON
        assert abs(rows[0].c_herd_now - 0.6) < EPSILON

        result = _TRUST_SVC.compute_oracle_trust(rows=rows, t_now=1000)
        assert abs(result - 0.85) < EPSILON

    async def test_lag_picks_immediate_predecessor_not_first(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        """LAG window picks the immediately preceding attestation's c_herd.

        H1 has three attestations: B(ts=100), C(ts=200), A(ts=300).
        c_herd_prior for A should be C's c_herd (0.6), not B's (0.3).
        c_herd_now should be A's own c_herd (0.7) since A is latest.

        n_oracle_prior = 2 (B, C), N_O = 3, M_write = 3/4 = 0.75.
        align_write = 1 - 0.5*|0.5 - 0.6| = 0.95
        align_read  = 1 - 0.5*|0.5 - 0.7| = 0.9
        align       = 0.75*0.95 + 0.25*0.9 = 0.9375
        info        = 1 - 0.6 = 0.4
        effective_align = 0.4*0.9375 + 0.6*0.5 = 0.675
        Single row → t_oracle = 0.675.
        """
        h_id = await seed_hypothesis(hypothesis_repo)

        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-B",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=100,
                t_oracle=0.5,
                c_oracle_raw=0.3,
                c_oracle_discounted=0.15,
                c_herd=0.3,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-C",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=200,
                t_oracle=0.5,
                c_oracle_raw=0.6,
                c_oracle_discounted=0.3,
                c_herd=0.6,
                n_oracle_prior=1,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=300,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.7,
                n_oracle_prior=2,
            )
        )

        rows = await attestations_repo.fetch_trust_alignments(
            oracle_id="sub:oracle-A",
            t_now=300,
            trust_half_life=_NO_DECAY_HL,
        )
        assert len(rows) == 1
        # LAG picks C's c_herd (0.6), not B's (0.3)
        assert abs(rows[0].c_herd_prior - 0.6) < EPSILON
        # FIRST_VALUE DESC picks A's own c_herd (latest)
        assert abs(rows[0].c_herd_now - 0.7) < EPSILON

        result = _TRUST_SVC.compute_oracle_trust(rows=rows, t_now=300)
        assert abs(result - 0.675) < EPSILON

    async def test_first_value_picks_latest_after_oracle(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        """FIRST_VALUE DESC picks the latest c_herd, even from a later attester.

        H1: A attests (ts=100), then D attests (ts=200).
        c_herd_now for A should be D's c_herd (0.8), not A's own (0.4).
        The herd moved after A spoke — read-time alignment captures this.

        n_oracle_prior = 0 (D came after, A self-excluded), N_O = 1, M_write = 0.5.
        align_write = 1 - 0.5*|0.4 - 0.0| = 0.8
        align_read  = 1 - 0.5*|0.4 - 0.8| = 0.8
        align       = 0.5*0.8 + 0.5*0.8 = 0.8
        info        = 1 - 0 = 1.0 → effective_align = 0.8.
        Single row → t_oracle = 0.8.
        """
        h_id = await seed_hypothesis(hypothesis_repo)

        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=100,
                t_oracle=0.5,
                c_oracle_raw=0.4,
                c_oracle_discounted=0.2,
                c_herd=0.4,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-D",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=200,
                t_oracle=0.5,
                c_oracle_raw=0.8,
                c_oracle_discounted=0.4,
                c_herd=0.8,
                n_oracle_prior=1,
            )
        )

        rows = await attestations_repo.fetch_trust_alignments(
            oracle_id="sub:oracle-A",
            t_now=200,
            trust_half_life=_NO_DECAY_HL,
        )
        assert len(rows) == 1
        assert abs(rows[0].c_herd_prior - 0.0) < EPSILON
        # D's later attestation is the current herd state
        assert abs(rows[0].c_herd_now - 0.8) < EPSILON

        result = _TRUST_SVC.compute_oracle_trust(rows=rows, t_now=200)
        assert abs(result - 0.8) < EPSILON

    async def test_decay_weighting_recent_dominates(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        """Recent attestations dominate old ones via exponential decay.

        trust_half_life = 2000s, window = 5*2000 = 10000, window_start = 0.
        Both attestations within the window. t_now = 10000.

        H1 (old, poor alignment):
          Oracle B seeds (ts=50), Oracle A attests (ts=100).
        H2 (recent, good alignment):
          Oracle C seeds (ts=8900), Oracle A attests (ts=9000).
        """
        trust_half_life = 2000.0
        t_now = 10000

        h1_id = await seed_hypothesis(hypothesis_repo)
        h2_id = await seed_hypothesis(hypothesis_repo)

        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h1_id,
                oracle_id="sub:oracle-B",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=50,
                t_oracle=0.5,
                c_oracle_raw=0.3,
                c_oracle_discounted=0.15,
                c_herd=0.3,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h1_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=100,
                t_oracle=0.5,
                c_oracle_raw=-0.8,
                c_oracle_discounted=-0.4,
                c_herd=-0.3,
                n_oracle_prior=1,
            )
        )

        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h2_id,
                oracle_id="sub:oracle-C",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=8900,
                t_oracle=0.5,
                c_oracle_raw=-0.2,
                c_oracle_discounted=-0.1,
                c_herd=-0.2,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h2_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=9000,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.5,
                n_oracle_prior=1,
            )
        )

        rows = await attestations_repo.fetch_trust_alignments(
            oracle_id="sub:oracle-A",
            t_now=t_now,
            trust_half_life=trust_half_life,
        )
        assert len(rows) == 2

        # Compute expected value (conviction-weighted, with adaptive w + info weighting).
        lambda_trust = math.log(2) / trust_half_life
        # H1 row: n_oracle_prior=1 (B), N_O=2, M_write=2/3.
        #   align_write = 1 - 0.5*|-0.8 - 0.3|  = 0.45
        #   align_read  = 1 - 0.5*|-0.8 - -0.3| = 0.75
        #   align       = (2/3)*0.45 + (1/3)*0.75 = 0.55
        #   info        = 1 - 0.3 = 0.7
        #   effective   = 0.7*0.55 + 0.3*0.5    = 0.535
        eff_h1 = 0.7 * ((2 / 3) * 0.45 + (1 / 3) * 0.75) + 0.3 * 0.5
        conv_h1 = 0.8  # |c_oracle_raw|
        weight_h1 = math.exp(-lambda_trust * (t_now - 100))
        # H2 row: n_oracle_prior=1 (C), N_O=2, M_write=2/3.
        #   align_write = 1 - 0.5*|0.5 - -0.2| = 0.65
        #   align_read  = 1 - 0.5*|0.5 -  0.5| = 1.00
        #   align       = (2/3)*0.65 + (1/3)*1.0 ≈ 0.7667
        #   info        = 1 - 0.2 = 0.8
        #   effective   = 0.8*0.7667 + 0.2*0.5 ≈ 0.7133
        eff_h2 = 0.8 * ((2 / 3) * 0.65 + (1 / 3) * 1.0) + 0.2 * 0.5
        conv_h2 = 0.5  # |c_oracle_raw|
        weight_h2 = math.exp(-lambda_trust * (t_now - 9000))
        expected = (eff_h1 * conv_h1 * weight_h1 + eff_h2 * conv_h2 * weight_h2) / (
            conv_h1 * weight_h1 + conv_h2 * weight_h2
        )

        svc = MathService(c_half_life=1e12, t_half_life=trust_half_life)
        result = svc.compute_oracle_trust(rows=rows, t_now=t_now)

        # Recent (well-aligned) attestation dominates the stale conflicting one.
        assert result > eff_h1
        assert abs(result - expected) < EPSILON

    async def test_time_bounded_excludes_old_attestations(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        """Attestations beyond 5x trust_half_life are excluded entirely.

        trust_half_life = 1000, window = 5000. t_now = 10000.
        Attestation at ts=100:  Dt=9900 > 5000 -> EXCLUDED (poor alignment)
        Attestation at ts=6000: Dt=4000 < 5000 -> INCLUDED (good alignment)

        Included row: n_oracle_prior=1 (C), N_O=2, M_write=2/3.
        c_oracle_raw=0.5, c_herd_prior=0.5, c_herd_now=0.5.
        align_write = align_read = 1.0 → align = 1.0.
        info = 1 - 0.5 = 0.5 → effective_align = 0.5*1.0 + 0.5*0.5 = 0.75.
        Single row → t_oracle = 0.75.
        """
        trust_half_life = 1000.0
        t_now = 10000

        h1_id = await seed_hypothesis(hypothesis_repo)
        h2_id = await seed_hypothesis(hypothesis_repo)

        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h1_id,
                oracle_id="sub:oracle-B",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=50,
                t_oracle=0.5,
                c_oracle_raw=0.9,
                c_oracle_discounted=0.45,
                c_herd=0.9,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h1_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=100,
                t_oracle=0.5,
                c_oracle_raw=-0.9,
                c_oracle_discounted=-0.45,
                c_herd=0.0,
                n_oracle_prior=1,
            )
        )

        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h2_id,
                oracle_id="sub:oracle-C",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=5900,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.5,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h2_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=6000,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.5,
                n_oracle_prior=1,
            )
        )

        rows = await attestations_repo.fetch_trust_alignments(
            oracle_id="sub:oracle-A",
            t_now=t_now,
            trust_half_life=trust_half_life,
        )
        # Only the recent attestation (ts=6000) should be returned
        assert len(rows) == 1
        assert rows[0].timestamp == 6000

        svc = MathService(c_half_life=1e12, t_half_life=trust_half_life)
        result = svc.compute_oracle_trust(rows=rows, t_now=t_now)
        assert abs(result - 0.75) < EPSILON

    async def test_infinite_half_life_includes_all_history(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        """trust_half_life=inf is the "no decay" mode — no time floor.

        Regression: int(5 * inf) used to raise OverflowError at the SQL
        boundary. An ancient (timestamp=1) attestation that would be
        excluded under any finite half-life with t_now=10_000 proves the
        window's lower bound truly collapses.
        """
        h_id = await seed_hypothesis(hypothesis_repo)
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-B",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=100,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.5,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=1,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.5,
                n_oracle_prior=0,
            )
        )

        rows = await attestations_repo.fetch_trust_alignments(
            oracle_id="sub:oracle-A",
            t_now=10_000,
            trust_half_life=math.inf,
        )

        assert len(rows) == 1
        assert rows[0].timestamp == 1

    async def test_trust_scan_reads_stored_n_oracle_prior_value_not_derived(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        """The trust scan returns the ``n_oracle_prior`` the Recorder stored.

        Only one oracle has ever attested on the hypothesis, so a derived
        count would be 0. The fixture stores 99 — a value impossible under
        derivation. The assertion that 99 comes back proves the SQL reads
        the stored column rather than recomputing it.
        """
        h_id = await seed_hypothesis(hypothesis_repo)

        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-A",
                correlation_id=_DEFAULT_CORRELATION_ID,
                timestamp=100,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.5,
                n_oracle_prior=99,
            )
        )

        rows = await attestations_repo.fetch_trust_alignments(
            oracle_id="sub:oracle-A",
            t_now=200,
            trust_half_life=_NO_DECAY_HL,
        )
        assert len(rows) == 1
        assert rows[0].n_oracle_prior == 99
