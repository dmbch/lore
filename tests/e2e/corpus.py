"""Seed corpus for the golden e2e archive.

Rebuild triggers: corpus, prompt, model, or epistemics/trust-math
changes. The fixture bakes write-time ledger math (t_oracle,
c_oracle_discounted, c_herd); a trust-pipeline change silently
invalidates it. Run `mise run golden-rebuild` to regenerate
`tests/e2e/fixtures/golden.db.gz`. The recall eval's labeled queries bind
to these seeds by correlation ID (see `tests/e2e/queries.py`), so a
rebuild that collapses or splits a seed moves what those labels resolve
to. The pinned model version is a rebuild trigger too: the archive is
seeded through the live pipeline.

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
    # Distractors. Retrieval fails by confusion, not by volume: an archive
    # of unrelated facts leaves the labeled targets winning by a mile, and
    # recall measures nothing. These crowd the labeled targets in both
    # lanes, sharing their entities and sentence shapes without
    # paraphrasing them (a paraphrase would corroborate into the target's
    # node and grow the archive by nothing) and without contradicting them
    # at an overlapping reference time (which would move the target's
    # epistemic state and the e2e assertions that read it). They appear
    # last so every seed above still resolves against exactly what it did
    # before. They carry no query labels: their whole job is to compete.
    Seed(
        correlation_id="distractor-db-pooler",
        oracle="oracle-corpus-a",
        hypothesis="Database B's connection pooling is handled by PgBouncer.",
        confidence=0.7,
    ),
    Seed(
        correlation_id="distractor-db-mysql",
        oracle="oracle-corpus-b",
        hypothesis="Database C is built on MySQL.",
        confidence=0.7,
    ),
    Seed(
        correlation_id="distractor-db-replica",
        oracle="oracle-corpus-a",
        hypothesis="Database B serves a read replica from the Frankfurt region.",
        confidence=0.6,
    ),
    Seed(
        correlation_id="distractor-http-mtls",
        oracle="oracle-corpus-b",
        hypothesis="The HTTP service authenticates inbound requests with mutual TLS.",
        confidence=0.7,
    ),
    Seed(
        correlation_id="distractor-grpc-gateway",
        oracle="oracle-corpus-c",
        hypothesis="gRPC streaming is disabled on the public API gateway.",
        confidence=0.6,
    ),
    Seed(
        correlation_id="distractor-kafka",
        oracle="oracle-corpus-a",
        hypothesis="The batch ingestion service moves messages over Apache Kafka.",
        confidence=0.7,
    ),
    Seed(
        correlation_id="distractor-jupiter-orbit",
        oracle="oracle-corpus-c",
        hypothesis="Jupiter completes one orbit of the Sun in roughly twelve Earth years.",
        confidence=0.8,
    ),
    Seed(
        correlation_id="distractor-ecliptic",
        oracle="oracle-corpus-b",
        hypothesis="The planets of the solar system share a common orbital plane, the ecliptic.",
        confidence=0.8,
    ),
    Seed(
        correlation_id="distractor-meridian-floors",
        oracle="oracle-corpus-a",
        hypothesis="The Meridian Tower in Harborview has forty-two floors.",
        confidence=0.7,
    ),
    Seed(
        correlation_id="distractor-harborview-centre",
        oracle="oracle-corpus-c",
        hypothesis="Harborview's convention centre stands on the eastern waterfront.",
        confidence=0.6,
    ),
    Seed(
        correlation_id="distractor-unemployment-2010",
        oracle="oracle-corpus-b",
        hypothesis="As of 2010, the national unemployment rate is 9 percent.",
        confidence=0.8,
    ),
    Seed(
        correlation_id="distractor-capgains-2015",
        oracle="oracle-corpus-a",
        hypothesis="As of 2015, the United States federal capital gains tax rate is 20 percent.",
        confidence=0.8,
    ),
    Seed(
        correlation_id="distractor-convention-1787",
        oracle="oracle-corpus-c",
        hypothesis=(
            "The 1787 Constitutional Convention in Philadelphia drafted the "
            "United States Constitution."
        ),
        confidence=0.8,
    ),
    Seed(
        correlation_id="distractor-nineteenth-amendment",
        oracle="oracle-corpus-b",
        hypothesis=(
            "The Nineteenth Amendment to the United States Constitution was ratified in 1920."
        ),
        confidence=0.8,
    ),
    Seed(
        correlation_id="distractor-valletta-cadets",
        oracle="oracle-corpus-a",
        hypothesis="The Valletta maritime academy admits sixty cadets each year.",
        confidence=0.7,
    ),
    Seed(
        correlation_id="distractor-trieste-language",
        oracle="oracle-corpus-c",
        hypothesis="The language of instruction at the Trieste naval college is Italian.",
        confidence=0.8,
    ),
    Seed(
        correlation_id="distractor-cedarbrook-clinics",
        oracle="oracle-corpus-b",
        hypothesis="As of 2023, Cedarbrook Health operates four regional clinics.",
        confidence=0.7,
    ),
)
