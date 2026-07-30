"""Seed corpus for the golden e2e archive.

Rebuild triggers: corpus, prompt, model, or epistemics/trust-math
changes. The fixture bakes write-time ledger math (t_oracle,
c_oracle_discounted, c_herd); a trust-pipeline change silently
invalidates it. Run `mise run golden-rebuild` to regenerate
`tests/e2e/fixtures/golden.db.gz`. The future eval-harness corpus grows
from this module (see TODO.md).

Order is significant: later seeds resolve against earlier ones in the
archive. Seeds are stored fresh; temporal tests backdate at test time.
"""

from typing import NamedTuple


class Seed(NamedTuple):
    correlation_id: str
    oracle: str
    hypothesis: str
    confidence: float


SEEDS: tuple[Seed, ...] = (
    Seed(
        correlation_id="agg-scen1-seed",
        oracle="oracle-seeder",
        hypothesis="Database B is built on PostgreSQL",
        confidence=0.7,
    ),
    Seed(
        correlation_id="agg-scen2-seed-a",
        oracle="oracle-seeder-a",
        hypothesis="All planets in the solar system orbit clockwise as seen from the north pole",
        confidence=0.7,
    ),
    Seed(
        correlation_id="agg-scen2-seed-b",
        oracle="oracle-seeder-b",
        hypothesis="Mars's orbit is clockwise as seen from above the northern celestial hemisphere",
        confidence=0.7,
    ),
    Seed(
        correlation_id="agg-scen3-seed-1",
        oracle="oracle-seeder-1",
        hypothesis="The HTTP service uses gRPC for internal RPC calls",
        confidence=0.7,
    ),
    Seed(
        correlation_id="agg-scen3-seed-2",
        oracle="oracle-seeder-2",
        hypothesis="Internal RPC traffic in the HTTP service is gRPC over HTTP/2",
        confidence=0.7,
    ),
    Seed(
        correlation_id="reftime-diff-seed",
        oracle="oracle-seeder",
        hypothesis="As of 2010, the national minimum wage is 7 dollars per hour.",
        confidence=0.8,
    ),
    Seed(
        correlation_id="reftime-same-seed",
        oracle="oracle-seeder",
        hypothesis="The tallest building in Harborview is the Meridian Tower.",
        confidence=0.8,
    ),
    Seed(
        correlation_id="reftime-mixed-event",
        oracle="oracle-seeder-e",
        hypothesis=(
            "The 1789 ratification of the Bill of Rights established the first ten amendments "
            "to the United States Constitution."
        ),
        confidence=0.8,
    ),
    Seed(
        correlation_id="reftime-mixed-value",
        oracle="oracle-seeder-v",
        hypothesis="As of 2015, the United States federal corporate tax rate is 35 percent.",
        confidence=0.8,
    ),
    Seed(
        correlation_id="aged-standing-seed",
        oracle="oracle-seeder",
        hypothesis="The language of instruction at the Valletta maritime academy is English.",
        confidence=0.8,
    ),
    Seed(
        correlation_id="aged-dated-seed",
        oracle="oracle-seeder",
        hypothesis="As of 2023, Cedarbrook Health employs 1,200 nurses.",
        confidence=0.8,
    ),
)
