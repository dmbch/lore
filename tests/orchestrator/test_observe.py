"""Observe read-path tests: the uncertainty frontier.

``observe.frontier`` fetches the most recently created hypotheses, enriches each
with its decayed ECBF herd confidence, and sorts by uncertainty descending. The
domain claim under test: the frontier surfaces where the herd knows least, so a
centaur's next attestation lands where it moves the needle most.

Single attestation at ``t_now`` (no decay) collapses to ``c_herd ==
c_oracle_discounted`` (ECBF of one opinion is that opinion), which keeps these
tests deterministic without hand-computing the fusion. The decay tests age that
single attestation by exactly one half-life, halving the belief mass
(``exp(-λ · t_half) = 0.5``, docs/logic.md "Decay").
"""

import time
from datetime import date

import pytest
from pydantic import ValidationError

from lore.domain import ArchivistOutput, DomainInvariantError, FrontierEntry
from lore.orchestrator import Orchestrator
from lore.orchestrator.observe import FRONTIER_LIMIT, frontier
from lore.providers import Providers
from lore.repositories import (
    AttestationRecord,
    HypothesisRecord,
    Repositories,
)

from .conftest import (
    StubAttestations,
    StubCache,
    StubCompletion,
    StubEmbedder,
    StubHypotheses,
    StubPool,
    StubRequests,
    make_attestation,
    make_interpreter_output,
    make_math,
    make_settings,
    raise_internal_validation_error,
)

_T = 2_000_000_000
_HALF_LIFE = 86400  # matches make_math's c_half_life


def _record(id: str, content: str = "claim") -> HypothesisRecord:
    return HypothesisRecord.model_construct(id=id, content=content, created_at=_T)


def _repos(
    records: list[HypothesisRecord],
    by_hypotheses: dict[str, list[AttestationRecord]] | None = None,
) -> Repositories:
    return Repositories(
        hypotheses=StubHypotheses(recent=records),
        attestations=StubAttestations(by_hypotheses=by_hypotheses),
        requests=StubRequests(),
        cache=StubCache(),
    )


async def test_frontier_empty_archive_returns_empty() -> None:
    entries = await frontier(repos=_repos([]), math=make_math(), limit=10, t_now=_T)
    assert entries == []


async def test_frontier_unattested_hypothesis_is_fully_uncertain() -> None:
    """No attestations means vacuous: c_herd = 0.0, uncertainty = 1.0."""
    record = _record("aaa00001-e29b-41d4-a716-446655440000")
    entries = await frontier(repos=_repos([record]), math=make_math(), limit=10, t_now=_T)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.c_herd == 0.0
    assert entry.uncertainty == 1.0
    assert entry.attestation_count == 0
    assert entry.last_attested is None


async def test_frontier_sorts_most_uncertain_first() -> None:
    """Most uncertain (smallest |c_herd|) leads; the frontier points at ignorance."""
    low = _record("aaa00001-e29b-41d4-a716-446655440000", "well known")
    mid = _record("aaa00002-e29b-41d4-a716-446655440000", "partly known")
    high = _record("aaa00003-e29b-41d4-a716-446655440000", "barely known")

    by_hypotheses = {
        low.id: [make_attestation(hypothesis_id=low.id, c_oracle_discounted=0.9, timestamp=_T)],
        mid.id: [make_attestation(hypothesis_id=mid.id, c_oracle_discounted=0.5, timestamp=_T)],
        high.id: [make_attestation(hypothesis_id=high.id, c_oracle_discounted=0.2, timestamp=_T)],
    }
    # find_recent order is deliberately not the sorted order.
    repos = _repos([low, mid, high], by_hypotheses=by_hypotheses)

    entries = await frontier(repos=repos, math=make_math(), limit=10, t_now=_T)

    assert [e.id for e in entries] == [high.id, mid.id, low.id]
    assert [e.uncertainty for e in entries] == sorted(
        (e.uncertainty for e in entries), reverse=True
    )
    # Single attestation at t_now: c_herd collapses to c_oracle_discounted.
    assert entries[0].c_herd == pytest.approx(0.2)
    assert entries[2].c_herd == pytest.approx(0.9)
    assert entries[0].attestation_count == 1
    # _T = 2_000_000_000 epoch seconds is 2033-05-18 UTC: the newest
    # attestation surfaces as a calendar date, same idiom as SearchResult.
    assert entries[0].last_attested == date(2033, 5, 18)


async def test_frontier_decays_stale_attestations() -> None:
    """One half-life of age halves the herd signal: c 0.8 → 0.4, u 0.2 → 0.6."""
    record = _record("aaa00001-e29b-41d4-a716-446655440000")
    by_hypotheses = {
        record.id: [
            make_attestation(
                hypothesis_id=record.id, c_oracle_discounted=0.8, timestamp=_T - _HALF_LIFE
            )
        ]
    }
    repos = _repos([record], by_hypotheses=by_hypotheses)

    entries = await frontier(repos=repos, math=make_math(), limit=10, t_now=_T)

    assert entries[0].c_herd == pytest.approx(0.4)
    assert entries[0].uncertainty == pytest.approx(0.6)


async def test_frontier_breaks_uncertainty_ties_by_id_ascending() -> None:
    """Equal uncertainty resolves by id ascending: deterministic ordering."""
    later_id = _record("bbb00001-e29b-41d4-a716-446655440000")
    earlier_id = _record("aaa00001-e29b-41d4-a716-446655440000")
    # Both unattested => both uncertainty 1.0; find_recent yields them id-descending.
    repos = _repos([later_id, earlier_id])

    entries = await frontier(repos=repos, math=make_math(), limit=10, t_now=_T)

    assert [e.id for e in entries] == [earlier_id.id, later_id.id]


def _providers() -> Providers:
    return Providers(
        embedder=StubEmbedder(),
        interpreter=StubCompletion(make_interpreter_output()),
        archivist=StubCompletion(ArchivistOutput(reasoning="r", answer="a")),
    )


async def test_orchestrator_frontier_returns_entries_from_session() -> None:
    """Orchestrator.frontier opens a session and returns enriched entries."""
    record = _record("aaa00001-e29b-41d4-a716-446655440000", "a claim")
    orchestrator = Orchestrator(
        pool=StubPool(_repos([record])),
        providers=_providers(),
        math=make_math(),
        settings=make_settings(),
    )

    entries = await orchestrator.frontier()

    assert len(entries) == 1
    assert isinstance(entries[0], FrontierEntry)
    assert entries[0].content == "a claim"
    assert entries[0].uncertainty == 1.0


async def test_orchestrator_frontier_threads_wall_clock_into_decay() -> None:
    """An attestation one half-life old arrives halved: t_now is the wall clock.

    Loose tolerance absorbs the seconds between building the attestation and
    the orchestrator reading its own clock; it still rules out t_now=0
    (negative dt clamps, c_herd stays 0.8) and t_now far future (decays to ~0).
    """
    record = _record("aaa00001-e29b-41d4-a716-446655440000")
    aged = make_attestation(
        hypothesis_id=record.id,
        c_oracle_discounted=0.8,
        timestamp=int(time.time()) - _HALF_LIFE,
    )
    orchestrator = Orchestrator(
        pool=StubPool(_repos([record], by_hypotheses={record.id: [aged]})),
        providers=_providers(),
        math=make_math(),
        settings=make_settings(),
    )

    entries = await orchestrator.frontier()

    assert entries[0].c_herd == pytest.approx(0.4, abs=1e-3)
    assert entries[0].uncertainty == pytest.approx(0.6, abs=1e-3)


async def test_orchestrator_frontier_default_limit_is_frontier_limit() -> None:
    """The default bound is the module constant, threaded to find_recent."""
    records = [
        _record(f"aaa{i:05d}-e29b-41d4-a716-446655440000", f"claim {i}")
        for i in range(FRONTIER_LIMIT + 5)
    ]
    orchestrator = Orchestrator(
        pool=StubPool(_repos(records)),
        providers=_providers(),
        math=make_math(),
        settings=make_settings(),
    )

    entries = await orchestrator.frontier()

    assert len(entries) == FRONTIER_LIMIT


async def test_orchestrator_frontier_wraps_internal_validation_error_as_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same masking contract as consult: FrontierEntry construction is internal.

    A raw pydantic ValidationError escaping the orchestrator would reach
    fastmcp's echo arm, bypassing masking; the wrap keeps the cause for
    operators and the payload off the wire.
    """
    monkeypatch.setattr(
        "lore.orchestrator.observe.FrontierEntry",
        raise_internal_validation_error,
    )
    orchestrator = Orchestrator(
        pool=StubPool(_repos([_record("aaa00001-e29b-41d4-a716-446655440000")])),
        providers=_providers(),
        math=make_math(),
        settings=make_settings(),
    )

    with pytest.raises(DomainInvariantError) as exc_info:
        await orchestrator.frontier()

    assert isinstance(exc_info.value.__cause__, ValidationError)
