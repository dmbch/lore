"""Labeled queries for the retrieval-recall eval harness.

Authoring rules:

- `expected` entries are correlation IDs from `tests.e2e.corpus.SEEDS`.
  The eval driver resolves them to hypothesis IDs against the golden
  archive at runtime; paraphrase collapse may map several correlation
  IDs to one hypothesis (the scen3 pair does).
- No relative dates in query text. Spell dates absolutely; the golden
  archive is frozen, the eval runs later.
- Queries are read-only eval inputs. They never enter the archive.
- Labels are positive-only. Recall over absent labels is undefined;
  do not add negative controls expecting nothing.
"""

from typing import NamedTuple


class LabeledQuery(NamedTuple):
    id: str
    question: str | None
    hypothesis: str | None
    context: str | None
    expected: tuple[str, ...]


QUERIES: tuple[LabeledQuery, ...] = (
    LabeledQuery(
        id="database-engine",
        question="What database engine backs Database B?",
        hypothesis=None,
        context=None,
        expected=("agg-scen1-seed",),
    ),
    LabeledQuery(
        id="planetary-orbit-direction",
        question=None,
        hypothesis=(
            "Planets in the solar system orbit clockwise when viewed from above the north pole."
        ),
        context=None,
        expected=("agg-scen2-seed-a", "agg-scen2-seed-b"),
    ),
    # The stored scen3 hypotheses spell "HTTP" and "RPC" out (the seeding
    # normalizer expanded them, "gRPC" stays verbatim). The short-form pair
    # below retrieves only if keyword extraction bridges to the expansions.
    LabeledQuery(
        id="abbrev-bridge-question",
        question="Which protocol carries internal RPC traffic in the HTTP service?",
        hypothesis=None,
        context=None,
        expected=("agg-scen3-seed-1", "agg-scen3-seed-2"),
    ),
    LabeledQuery(
        id="abbrev-bridge-hypothesis",
        question=None,
        hypothesis="The HTTP service's RPC layer is built on gRPC.",
        context=None,
        expected=("agg-scen3-seed-1", "agg-scen3-seed-2"),
    ),
    LabeledQuery(
        id="tallest-building",
        question="What is the tallest building in Harborview?",
        hypothesis=None,
        context=None,
        expected=("reftime-same-seed",),
    ),
    LabeledQuery(
        id="minimum-wage-2010",
        question="What was the national minimum wage in 2010?",
        hypothesis=None,
        context=None,
        expected=("reftime-diff-seed",),
    ),
    LabeledQuery(
        id="bill-of-rights",
        question=(
            "When were the first ten amendments to the United States Constitution established?"
        ),
        hypothesis=None,
        context=None,
        expected=("reftime-mixed-event",),
    ),
    LabeledQuery(
        id="corporate-tax-2015",
        question="What was the United States federal corporate tax rate in 2015?",
        hypothesis=None,
        context=None,
        expected=("reftime-mixed-value",),
    ),
    LabeledQuery(
        id="academy-language",
        question="Which language is used for instruction at the maritime academy in Valletta?",
        hypothesis=None,
        context="Preparing exchange-program paperwork for a semester at the academy.",
        expected=("aged-standing-seed",),
    ),
    # Four domains in one composite: keyword demand beyond the prompt's cap
    # of 8, so this query guards against recall loss under keyword eviction.
    LabeledQuery(
        id="keyword-rich-composite",
        question=None,
        hypothesis=(
            "Database B runs on PostgreSQL, the Meridian Tower is the tallest building in "
            "Harborview, Cedarbrook Health employed 1,200 nurses as of 2023, and instruction "
            "at the Valletta maritime academy is in English."
        ),
        context=None,
        expected=(
            "agg-scen1-seed",
            "reftime-same-seed",
            "aged-dated-seed",
            "aged-standing-seed",
        ),
    ),
    # Abbreviations under cap pressure: the surface-form rule doubles HTTP
    # and RPC into pairs, pushing keyword demand past the prompt's cap of 8.
    # Guards seed-critical terms against pair-driven eviction; the plain
    # keyword-rich composite above cannot regress on that channel.
    LabeledQuery(
        id="abbrev-cap-composite",
        question=None,
        hypothesis=(
            "The HTTP service's internal RPC traffic runs on gRPC, Database B runs on "
            "PostgreSQL, and the Meridian Tower is the tallest building in Harborview."
        ),
        context=None,
        expected=(
            "agg-scen3-seed-1",
            "agg-scen3-seed-2",
            "agg-scen1-seed",
            "reftime-same-seed",
        ),
    ),
)
