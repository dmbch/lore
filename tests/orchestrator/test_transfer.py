"""Transfer-attestation tests — contradicts, multi-contradict, transfer shape."""

from lore.domain import (
    TRANSFER_ORACLE,
    ArchivistOutput,
    Resolution,
)
from tests.orchestrator.conftest import (
    make_attestation,
    make_hypothesis_result,
    make_math,
    make_orchestrator,
    write_request,
)


class TestWritePathNovelWithContradicts:
    async def test_write_path_novel_with_contradicts(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=hypothesis_id, content="old claim")
        attestation = make_attestation(hypothesis_id=hypothesis_id, oracle_id="oracle-2")

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={hypothesis_id: [attestation]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Contradicted.",
                resolutions=[
                    Resolution(contributes="the opposing claim", contradicts=[hypothesis_id])
                ],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.8),
            correlation_id="corr-1",
        )

        # Disbelief attestation on contradicted hypothesis
        disbelief_calls = [
            c for c in fixture.attestations.appended if c.hypothesis_id == hypothesis_id
        ]
        assert len(disbelief_calls) == 1
        assert disbelief_calls[0].c_oracle_raw == -0.8  # negated confidence

        # Novel hypothesis stored for the opposing claim
        assert len(fixture.hypotheses.stored) == 1
        content, _embedding, _created_at = fixture.hypotheses.stored[0]
        assert content == "the opposing claim"

        # Transfer + oracle attestations on the new hypothesis
        novel_calls = [c for c in fixture.attestations.appended if c.hypothesis_id != hypothesis_id]
        assert len(novel_calls) == 2
        transfer = next(c for c in novel_calls if c.oracle_id == TRANSFER_ORACLE)
        belief = next(c for c in novel_calls if c.oracle_id == "oracle-1")
        assert transfer.c_oracle_raw < 0  # negated herd state
        assert belief.c_oracle_raw == 0.8  # positive (belief)


class TestWritePathTransferAttestationShape:
    async def test_transfer_bypasses_discount_and_is_sole_attestation(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=hypothesis_id, content="old claim")
        attestation = make_attestation(hypothesis_id=hypothesis_id, oracle_id="oracle-2")

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={hypothesis_id: [attestation]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Contradicted.",
                resolutions=[Resolution(contributes="counter-claim", contradicts=[hypothesis_id])],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.8),
            correlation_id="corr-1",
        )

        novel_calls = [c for c in fixture.attestations.appended if c.hypothesis_id != hypothesis_id]
        transfer = next(c for c in novel_calls if c.oracle_id == TRANSFER_ORACLE)

        assert transfer.t_oracle == 1.0
        assert transfer.c_oracle_raw == transfer.c_oracle_discounted  # bypass discount
        assert transfer.c_oracle_raw == transfer.c_herd  # sole attestation
        assert transfer.c_oracle_raw < 0  # negated positive herd state

    async def test_transfer_is_negated_latest_c_herd_no_maturity(self) -> None:
        """Single contradict: c_transfer = -c_herd_latest, no maturity multiplier.

        Regression for the corrected transfer math — c_herd already encodes
        source-level discounts; applying M(h₁) again would double-discount.
        """
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=hypothesis_id)
        # Latest attestation row carries c_herd = 0.42; that is what transfer reads.
        attestation = make_attestation(
            hypothesis_id=hypothesis_id, oracle_id="oracle-a", c_herd=0.42
        )

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={hypothesis_id: [attestation]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Contradicted.",
                resolutions=[Resolution(contributes="counter-claim", contradicts=[hypothesis_id])],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.8),
            correlation_id="corr-1",
        )

        novel_calls = [c for c in fixture.attestations.appended if c.hypothesis_id != hypothesis_id]
        transfer = next(c for c in novel_calls if c.oracle_id == TRANSFER_ORACLE)

        # Decay since attestation timestamp is negligible (same second).
        # Expected: c_transfer = -c_herd_latest = -0.42 (no M factor).
        assert abs(transfer.c_oracle_raw - (-0.42)) < 1e-9
        # Sanity: not the maturity-scaled value either.
        # M(N_O=1, K=1) = 1/2; -0.5 * 0.42 = -0.21. Must NOT equal that.
        assert abs(transfer.c_oracle_raw - (-0.21)) > 1e-3

    async def test_transfer_and_oracle_share_timestamp_with_transfer_first(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=hypothesis_id, content="old claim")
        attestation = make_attestation(hypothesis_id=hypothesis_id, oracle_id="oracle-2")

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={hypothesis_id: [attestation]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Contradicted.",
                resolutions=[Resolution(contributes="counter-claim", contradicts=[hypothesis_id])],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.8),
            correlation_id="corr-1",
        )

        novel_calls = [c for c in fixture.attestations.appended if c.hypothesis_id != hypothesis_id]
        transfer = next(c for c in novel_calls if c.oracle_id == TRANSFER_ORACLE)
        oracle_att = next(c for c in novel_calls if c.oracle_id == "oracle-1")

        assert transfer.timestamp == oracle_att.timestamp
        # Insertion order: transfer appended before oracle.
        transfer_idx = fixture.attestations.appended.index(transfer)
        oracle_idx = fixture.attestations.appended.index(oracle_att)
        assert transfer_idx < oracle_idx


class TestWritePathOracleFusesAgainstTransfer:
    async def test_oracle_cherd_reflects_transfer(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=hypothesis_id, content="old claim")
        attestation = make_attestation(hypothesis_id=hypothesis_id, oracle_id="oracle-2")

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={hypothesis_id: [attestation]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Contradicted.",
                resolutions=[Resolution(contributes="counter-claim", contradicts=[hypothesis_id])],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.8),
            correlation_id="corr-1",
        )

        novel_calls = [c for c in fixture.attestations.appended if c.hypothesis_id != hypothesis_id]
        oracle_att = next(c for c in novel_calls if c.oracle_id == "oracle-1")

        # Without transfer, c_herd would be purely positive (oracle's discounted opinion).
        # With transfer, fusion pulls c_herd down.
        math = make_math()
        pure_novel = math.prepare_attestation(
            confidence=0.8, existing=[], t_now=oracle_att.timestamp, t_oracle=0.5, n_oracle_prior=0
        )
        assert oracle_att.c_herd < pure_novel.c_herd


class TestWritePathVacuousContradictionSkipsTransfer:
    async def test_vacuous_contradiction_skips_transfer(self) -> None:
        hypothesis_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=hypothesis_id, content="vacuous claim")
        # No existing attestations → c_herd = 0.0 → transfer would be 0

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={hypothesis_id: []},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Contradicted.",
                resolutions=[Resolution(contributes="counter-claim", contradicts=[hypothesis_id])],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.8),
            correlation_id="corr-1",
        )

        # Only disbelief on contradicted + oracle attestation on novel (no transfer)
        novel_calls = [c for c in fixture.attestations.appended if c.hypothesis_id != hypothesis_id]
        assert len(novel_calls) == 1
        assert novel_calls[0].oracle_id == "oracle-1"
        assert novel_calls[0].c_oracle_raw == 0.8


class TestWritePathContributeMultiContradictConsolidated:
    async def test_multi_contradict_writes_one_consolidated_transfer(self) -> None:
        h1 = "550e8400-e29b-41d4-a716-446655440000"
        h2 = "660e8400-e29b-41d4-a716-446655440000"
        r1 = make_hypothesis_result(id=h1, content="claim 1")
        r2 = make_hypothesis_result(id=h2, content="claim 2")
        a1 = make_attestation(hypothesis_id=h1, oracle_id="oracle-a", c_herd=0.4)
        a2 = make_attestation(hypothesis_id=h2, oracle_id="oracle-b", c_herd=0.6)

        fixture = make_orchestrator(
            search_results=[r1, r2],
            by_hypotheses={h1: [a1], h2: [a2]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Contradicts both.",
                resolutions=[Resolution(contributes="counter-claim", contradicts=[h1, h2])],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.8),
            correlation_id="corr-1",
        )

        novel_h_id = next(
            c.hypothesis_id
            for c in fixture.attestations.appended
            if c.hypothesis_id not in {h1, h2}
        )
        transfers = [
            c
            for c in fixture.attestations.appended
            if c.hypothesis_id == novel_h_id and c.oracle_id == TRANSFER_ORACLE
        ]
        # Exactly one consolidated transfer.
        assert len(transfers) == 1

        # Value: -ECBF over the latest c_herds, decayed to t_now.
        from lore.domain import EvidenceInput

        math = make_math()
        evidence = [
            EvidenceInput(c_oracle_discounted=0.4, timestamp=a1.timestamp),
            EvidenceInput(c_oracle_discounted=0.6, timestamp=a2.timestamp),
        ]
        expected = -math.compute_confidence(attestations=evidence, t_now=transfers[0].timestamp)
        assert abs(transfers[0].c_oracle_raw - expected) < 1e-9
        # Transfer carries full credibility, no second discount.
        assert transfers[0].t_oracle == 1.0
        assert transfers[0].c_oracle_raw == transfers[0].c_oracle_discounted

        # Disbelief on each contradicted hypothesis.
        for h_id in [h1, h2]:
            disbelief = [c for c in fixture.attestations.appended if c.hypothesis_id == h_id]
            assert len(disbelief) == 1
            assert disbelief[0].c_oracle_raw == -0.8


class TestWritePathContributeBalancedContradictsSkipsTransfer:
    async def test_balanced_contradicts_skip_transfer(self) -> None:
        h1 = "550e8400-e29b-41d4-a716-446655440000"
        h2 = "660e8400-e29b-41d4-a716-446655440000"
        r1 = make_hypothesis_result(id=h1, content="claim 1")
        r2 = make_hypothesis_result(id=h2, content="claim 2")
        # Symmetric herd opinions — fused result rounds to zero.
        a1 = make_attestation(hypothesis_id=h1, oracle_id="oracle-a", c_herd=0.5)
        a2 = make_attestation(hypothesis_id=h2, oracle_id="oracle-b", c_herd=-0.5)

        fixture = make_orchestrator(
            search_results=[r1, r2],
            by_hypotheses={h1: [a1], h2: [a2]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Contradicts both.",
                resolutions=[Resolution(contributes="counter-claim", contradicts=[h1, h2])],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.8),
            correlation_id="corr-1",
        )

        novel_h_id = next(
            c.hypothesis_id
            for c in fixture.attestations.appended
            if c.hypothesis_id not in {h1, h2}
        )
        transfers = [
            c
            for c in fixture.attestations.appended
            if c.hypothesis_id == novel_h_id and c.oracle_id == TRANSFER_ORACLE
        ]
        # No transfer row written — fused result is ≈ 0.
        assert len(transfers) == 0

        # Oracle's attestation on the novel still lands.
        oracle_atts = [
            c
            for c in fixture.attestations.appended
            if c.hypothesis_id == novel_h_id and c.oracle_id == "oracle-1"
        ]
        assert len(oracle_atts) == 1


class TestWritePathParaphraseWithContradicts:
    async def test_paraphrase_with_contradicts(self) -> None:
        corroborated_id = "550e8400-e29b-41d4-a716-446655440000"
        contradicted_id = "660e8400-e29b-41d4-a716-446655440000"
        cor_result = make_hypothesis_result(id=corroborated_id, content="paraphrased claim")
        con_result = make_hypothesis_result(id=contradicted_id, content="opposite claim")
        cor_att = make_attestation(hypothesis_id=corroborated_id, oracle_id="oracle-2")
        con_att = make_attestation(hypothesis_id=contradicted_id, oracle_id="oracle-3", c_herd=0.5)

        fixture = make_orchestrator(
            search_results=[cor_result, con_result],
            by_hypotheses={corroborated_id: [cor_att], contradicted_id: [con_att]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Paraphrase contradicting another.",
                resolutions=[
                    Resolution(corroborates=corroborated_id, contradicts=[contradicted_id])
                ],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.7),
            correlation_id="corr-1",
        )

        # No new hypothesis stored — paraphrase, not novel.
        assert len(fixture.hypotheses.stored) == 0

        # +c on corroborated, -c on contradicted, NO transfer row anywhere.
        cor_calls = [c for c in fixture.attestations.appended if c.hypothesis_id == corroborated_id]
        con_calls = [c for c in fixture.attestations.appended if c.hypothesis_id == contradicted_id]
        transfer_calls = [
            c for c in fixture.attestations.appended if c.oracle_id == TRANSFER_ORACLE
        ]
        assert len(cor_calls) == 1
        assert cor_calls[0].c_oracle_raw == 0.7
        assert len(con_calls) == 1
        assert con_calls[0].c_oracle_raw == -0.7
        assert len(transfer_calls) == 0


class TestWritePathContradictedNOraclePriorFromFullList:
    async def test_n_oracle_prior_excludes_current_oracle_when_present(self) -> None:
        contradicted_id = "550e8400-e29b-41d4-a716-446655440000"
        result = make_hypothesis_result(id=contradicted_id, content="old claim")
        # Three rows on contradicted: oracle-a (twice — should count once) and oracle-1
        # (the current oracle — must be excluded from n_oracle_prior).
        a1 = make_attestation(hypothesis_id=contradicted_id, oracle_id="oracle-a", c_herd=0.4)
        a2 = make_attestation(hypothesis_id=contradicted_id, oracle_id="oracle-a", c_herd=0.4)
        a3 = make_attestation(hypothesis_id=contradicted_id, oracle_id="oracle-1", c_herd=0.4)

        fixture = make_orchestrator(
            search_results=[result],
            by_hypotheses={contradicted_id: [a1, a2, a3]},
            archivist_output=ArchivistOutput(
                reasoning="test reasoning",
                answer="Contradicted.",
                resolutions=[
                    Resolution(contributes="counter-claim", contradicts=[contradicted_id])
                ],
            ),
        )

        await fixture.orchestrator.consult(
            oracle_id="oracle-1",
            request=write_request(confidence=0.8),
            correlation_id="corr-1",
        )

        # The negative attestation on the contradicted hypothesis must use
        # n_oracle_prior = 1 (distinct oracles excluding oracle-1: just oracle-a).
        # The math service adds +1 internally: N_O = 2, M = 2/3.
        # With t_oracle = 0.5 (default for empty trust history), P_effective = 1/3.
        # c_oracle_discounted = (1/3) * -0.8 ≈ -0.2667.
        # If n_oracle_prior were taken from a count proxy that did not exclude
        # the current oracle, N_O would be 3 and M = 3/4 → -0.3 — not what we want.
        disbelief = [
            c
            for c in fixture.attestations.appended
            if c.hypothesis_id == contradicted_id and c.oracle_id == "oracle-1"
        ]
        assert len(disbelief) == 1
        assert abs(disbelief[0].c_oracle_discounted - (-0.8 / 3)) < 1e-9
        # Sanity: not the count-proxy value (-0.3) either.
        assert abs(disbelief[0].c_oracle_discounted - (-0.3)) > 1e-3
