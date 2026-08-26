"""Real-backend test: the Recorder's ``c_herd`` fuses the full prior history.

Companion to ``test_attestation_fetch_keys_equal_union`` in
``tests/orchestrator/test_telemetry.py``: that test catches an N+1 perf
regression in the write-path fetch; this one pins the algebra. When a new
attestation lands on a hypothesis with priors, the persisted ``c_herd``
must equal ``MathService.prepare_attestation`` over the full prior set
plus the new opinion.
"""

from typing import Any, cast

from lore.domain import (
    ArchivistOutput,
    ConsultLoreRequest,
    EvidenceInput,
    InterpreterOutput,
    Resolution,
)
from lore.orchestrator import Orchestrator
from lore.providers import Providers
from lore.repositories import AttestationRecord
from lore.repositories._records import generate_id
from tests.repositories._orchestrator_fixtures import (
    FixedEmbedder,
    StubCompletion,
    make_math,
    make_settings,
)
from tests.repositories.conftest import BackendFixture, seed_request


async def test_recorder_passes_full_prior_history_to_prepare_attestation(
    backend: BackendFixture,
) -> None:
    """A corroborate from a fresh oracle (no trust history) on a hypothesis
    with two priors feeds ``(prior_A, prior_B)`` into ``prepare_attestation``.

    The persisted row equals what ``MathService.prepare_attestation``
    computes given the full prior set, proving the Recorder doesn't drop
    priors, doesn't pass only the latest row, and roundtrips through
    persistence without precision loss. The ECBF algebra itself is
    verified in ``tests/math/``; this test pins the integration wiring.

    ``t_now`` is captured from the persisted row's ``timestamp`` (the
    recorder writes ``timestamp=self._t_now``), so the expected and actual
    derivations share the orchestrator's real clock. ``t_oracle = 0.5`` is
    the empty-rows branch in ``compute_oracle_trust``: oracle-C has no
    prior attestations to scan. ``n_oracle_prior = 2`` since oracle-A and
    oracle-B are both distinct from oracle-C.
    """
    prior_a_id = "00000000-0000-0000-0000-000000000a01"
    prior_b_id = "00000000-0000-0000-0000-000000000a02"
    write_correlation_id = "00000000-0000-0000-0000-00000000c0fb"

    existing = await backend.hypotheses.store(
        content="an existing claim", embedding=[0.1] * 1024, created_at=0
    )

    # The seeded ``c_herd`` columns are storage defaults: the contract under
    # test is what the Recorder computes from ``c_oracle_discounted`` and
    # ``timestamp``, not what the prior ``c_herd`` fields say.
    await seed_request(backend.requests, correlation_id=prior_a_id, timestamp=1000)
    await backend.attestations.append(
        AttestationRecord(
            id=generate_id(),
            hypothesis_id=existing.id,
            oracle_id="oracle-A",
            correlation_id=prior_a_id,
            timestamp=1000,
            t_oracle=0.5,
            c_oracle_raw=0.5,
            c_oracle_discounted=0.3,
            c_herd=0.4,
            n_oracle_prior=0,
        )
    )
    await seed_request(backend.requests, correlation_id=prior_b_id, timestamp=2000)
    await backend.attestations.append(
        AttestationRecord(
            id=generate_id(),
            hypothesis_id=existing.id,
            oracle_id="oracle-B",
            correlation_id=prior_b_id,
            timestamp=2000,
            t_oracle=0.5,
            c_oracle_raw=0.5,
            c_oracle_discounted=-0.2,
            c_herd=0.4,
            n_oracle_prior=0,
        )
    )

    interpreter = StubCompletion(
        InterpreterOutput(
            question="normalized question",
            propositions=["the original proposition"],
            keywords=["kw"],
        )
    )
    archivist = StubCompletion(
        ArchivistOutput(
            reasoning="r",
            answer="a",
            resolutions=[Resolution(corroborates=existing.id)],
        )
    )
    providers = Providers(
        embedder=cast("Any", FixedEmbedder()),
        interpreter=cast("Any", interpreter),
        archivist=cast("Any", archivist),
    )
    math_service = make_math()
    orchestrator = Orchestrator(
        pool=backend.pool,
        providers=providers,
        math=math_service,
        settings=make_settings(),
    )

    await orchestrator.consult(
        oracle_id="oracle-C",
        request=ConsultLoreRequest(
            question="What is X?",
            hypothesis="X is a service",
            confidence=0.5,
        ),
        correlation_id=write_correlation_id,
    )

    rows = await backend.attestations.find_by_hypothesis(existing.id)
    new_rows = [a for a in rows if a.correlation_id == write_correlation_id]
    assert len(new_rows) == 1
    new_attestation = new_rows[0]

    expected = math_service.prepare_attestation(
        confidence=0.5,
        existing=[
            EvidenceInput(c_oracle_discounted=0.3, timestamp=1000),
            EvidenceInput(c_oracle_discounted=-0.2, timestamp=2000),
        ],
        t_now=new_attestation.timestamp,
        t_oracle=0.5,
        n_oracle_prior=2,
    )

    # Assert every field the Recorder derives, so a mismatch surfaces which
    # limb of the pipeline drifted (trust discount vs. ECBF fusion).
    assert new_attestation.t_oracle == expected.t_oracle
    assert new_attestation.c_oracle_raw == expected.c_oracle_raw
    assert new_attestation.c_oracle_discounted == expected.c_oracle_discounted
    assert new_attestation.c_herd == expected.c_herd
