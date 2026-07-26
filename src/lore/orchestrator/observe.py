"""Observe read path: the current uncertainty frontier.

The frontier is the `limit` most recently created hypotheses, each enriched with
its decayed ECBF herd confidence and sorted by uncertainty descending: where the
herd knows least sits on top, so the next attestation lands where it moves the
needle most.

Recency-by-`created_at` is the spike's honest bound. The full feature revisits
the O(archive) fusion cost of a true archive-wide frontier (see TODO.md).
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lore.domain import EvidenceInput, FrontierEntry
from lore.math import MathService
from lore.repositories import DecayWindow, Repositories

if TYPE_CHECKING:
    from lore.config import LoreSettings

FRONTIER_LIMIT = 25


async def frontier(
    *,
    repos: Repositories,
    math: MathService,
    settings: LoreSettings,
    limit: int,
    t_now: int,
) -> list[FrontierEntry]:
    """Return the current uncertainty frontier, most uncertain first.

    Fetches the newest `limit` hypotheses, fuses each hypothesis's ledger into a
    herd confidence scalar (decay + ECBF at read time), and sorts by uncertainty
    descending with id ascending as a deterministic tiebreak. The ledger fetch
    is decay-windowed; the view's full-history aggregates keep count and
    last_attested exact.
    """
    records = await repos.hypotheses.find_recent(limit=limit)
    if not records:
        return []

    window = DecayWindow(t_now=t_now, half_life=settings.epistemics.attestation_half_life)
    attestation_map = await repos.attestations.find_by_hypotheses(
        [r.id for r in records], window=window
    )

    entries: list[FrontierEntry] = []
    for record in records:
        view = attestation_map[record.id]
        evidence = [
            EvidenceInput(c_oracle_discounted=a.c_oracle_discounted, timestamp=a.timestamp)
            for a in view.rows
        ]
        c_herd = math.compute_confidence(attestations=evidence, t_now=t_now) if evidence else 0.0
        last_attested = (
            datetime.fromtimestamp(view.last_attested, tz=UTC).date()
            if view.last_attested is not None
            else None
        )
        entries.append(
            FrontierEntry(
                id=record.id,
                content=record.content,
                c_herd=c_herd,
                uncertainty=math.compute_uncertainty(c_herd),
                attestation_count=view.attestation_count,
                last_attested=last_attested,
            )
        )

    entries.sort(key=lambda e: (-e.uncertainty, e.id))
    return entries
