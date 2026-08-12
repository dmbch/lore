"""Score two-lane retrieval against the labeled query set in tests/e2e/queries.py.

The scoring core is pure: per-expected-hypothesis ranks in, recall and MRR
aggregates and a worst-first table out. ``main()`` is the live, metered
driver: it composes a read-only stack over a fresh golden-archive copy and,
per labeled query, runs one interpret call, one embedding batch, and three
search passes (composite plus each lane isolated). It never reasons and never
records; the archive is only read.

Artifacts persist every run: one JSONL receipt per query (interpreted
keywords, propositions, per-expected ranks, composite result IDs) at
recall.jsonl, in a fresh lore-recall-* tempdir (path printed) unless
--artifacts DIR places them deliberately. --prompt PATH swaps only the
interpreter prompt: two runs against the same frozen archive measure an
old-vs-candidate prompt delta.
"""

import argparse
import asyncio
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from lore._pydantic import DataModel
from lore.config import LoreSettings, load_settings
from lore.domain import ConsultLoreRequest, InterpreterOutput

# Deep imports by contract: the orchestrator barrel exports only the
# Orchestrator, and the eval must run the production stages, not a copy.
# Measurement tooling rides the internal stage seam; the commit gate reds
# if that seam moves.
from lore.orchestrator.interpret import interpret
from lore.orchestrator.retrieve import embed_sources, search_candidates
from lore.providers import Providers, build_providers, resolve_dimensions
from lore.repositories import (
    HypothesisRepository,
    RepositoryPool,
    check_health,
    connect,
    run_migrations,
)

if TYPE_CHECKING:
    from tests.e2e.queries import LabeledQuery


class ExpectedEntry(DataModel):
    """One expected label in a receipt row: joined seed labels, joined hypothesis
    set, and the set's best rank per lane."""

    correlation_id: str
    hypothesis_id: str
    composite: int | None
    proximity: int | None
    authority: int | None


class QueryScore(NamedTuple):
    query_id: str
    outcomes: tuple[ExpectedEntry, ...]


class ReceiptRow(DataModel):
    """One recall.jsonl line: the receipt contract the protocol driver consumes."""

    query_id: str
    keywords: tuple[str, ...]
    propositions: tuple[str, ...]
    expected: tuple[ExpectedEntry, ...]
    composite_results: tuple[str, ...]


class ArtifactLayout(NamedTuple):
    recall_log: Path


def artifact_layout(root: Path) -> ArtifactLayout:
    return ArtifactLayout(recall_log=root / "recall.jsonl")


def rank_of(hypothesis_id: str, *, results: Sequence[str]) -> int | None:
    for position, result in enumerate(results, start=1):
        if result == hypothesis_id:
            return position
    return None


def _all_outcomes(scores: Sequence[QueryScore]) -> list[ExpectedEntry]:
    return [outcome for score in scores for outcome in score.outcomes]


def _found(outcomes: Sequence[ExpectedEntry]) -> int:
    return sum(1 for outcome in outcomes if outcome.composite is not None)


def recall_at_limit(scores: Sequence[QueryScore]) -> float:
    # Every search pass truncates to the configured limit before per-source
    # pools merge, so a non-None composite rank means the hypothesis reached
    # the pool the Archivist would receive. Zero expectations reads as zero
    # recall: an eval that scored nothing must not read as perfect.
    outcomes = _all_outcomes(scores)
    if not outcomes:
        return 0.0
    return _found(outcomes) / len(outcomes)


def _best_reciprocal(score: QueryScore) -> float:
    found = [o.composite for o in score.outcomes if o.composite is not None]
    return 1 / min(found) if found else 0.0


def mean_reciprocal_rank(scores: Sequence[QueryScore]) -> float:
    """Textbook MRR (Voorhees 1999, TREC-8 QA track).

    Per query, the reciprocal of the best composite rank among its expected
    hypotheses; a query with none found contributes zero. Mean over queries.
    """
    if not scores:
        return 0.0
    return sum(_best_reciprocal(score) for score in scores) / len(scores)


def _found_fraction(score: QueryScore) -> float:
    # A query with no expectations carries no evidence of a retrieval miss;
    # fraction 1.0 sinks it below every query that actually missed one.
    outcomes = score.outcomes
    return _found(outcomes) / len(outcomes) if outcomes else 1.0


def _cell(rank: int | None) -> str:
    return str(rank) if rank is not None else "-"


def _ranks(outcome: ExpectedEntry) -> str:
    return (
        f"{outcome.hypothesis_id} c={_cell(outcome.composite)}"
        f" p={_cell(outcome.proximity)} a={_cell(outcome.authority)}"
    )


def format_table(scores: Sequence[QueryScore]) -> str:
    rows: list[str] = []
    for score in sorted(scores, key=lambda s: (_found_fraction(s), s.query_id)):
        details = "  ".join(_ranks(outcome) for outcome in score.outcomes)
        rows.append(f"{_found(score.outcomes)}/{len(score.outcomes)}  {score.query_id}  {details}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Live driver. The settings helpers are pure and unit-tested; the paid path
# below stays untested, like rate.py's main. Imports from tests/ are deferred
# into the driver functions: `python scripts/recall.py` resolves them only
# after main() has bootstrapped sys.path.
# ---------------------------------------------------------------------------


class LaneVariants(NamedTuple):
    proximity_only: LoreSettings
    authority_only: LoreSettings


def lane_variants(settings: LoreSettings) -> LaneVariants:
    """Build one settings variant per isolated retrieval lane.

    Only the two weights move, and each variant's weights still sum to 1.0; limit,
    fan-out, and everything outside retrieval stay untouched.
    """

    def isolate(*, proximity: float, authority: float) -> LoreSettings:
        retrieval = settings.retrieval.model_copy(
            update={"proximity": proximity, "authority": authority}
        )
        return settings.model_copy(update={"retrieval": retrieval})

    return LaneVariants(
        proximity_only=isolate(proximity=1.0, authority=0.0),
        authority_only=isolate(proximity=0.0, authority=1.0),
    )


def with_interpreter_prompt(settings: LoreSettings, *, prompt: Path) -> LoreSettings:
    """Swap only the interpreter prompt path: the old-vs-candidate delta knob."""
    prompts = settings.prompts.model_copy(update={"interpreter": prompt})
    return settings.model_copy(update={"prompts": prompts})


def resolve_expected(
    *, db_path: Path, oracles: Mapping[str, str], labeled: Iterable[str]
) -> dict[str, tuple[str, ...]]:
    """Resolve each labeled correlation ID to its stored hypothesis set.

    Mirrors the e2e conftest's ``golden_seed_id`` without private pool access:
    a seed consult stores under the seed's own oracle (the oracle filter keeps
    same-consult ``_transfer`` rows out). Rebuilds move content in both
    directions, so a seed may resolve to several hypotheses (decomposition
    split) and several seeds may share one (paraphrase collapse); only a seed
    resolving to nothing is a broken label. Plain sqlite3 on the decompressed
    copy, before the pool opens.
    """
    conn = sqlite3.connect(db_path)
    try:
        resolved: dict[str, tuple[str, ...]] = {}
        for correlation_id in sorted(labeled):
            rows = conn.execute(
                "SELECT DISTINCT hypothesis_id FROM attestations"
                " WHERE correlation_id = ? AND oracle_id = ?",
                (correlation_id, oracles[correlation_id]),
            ).fetchall()
            if not rows:
                msg = (
                    f"seed {correlation_id!r} resolves to no hypothesis;"
                    " rebuild the golden archive or fix the query labels"
                )
                raise ValueError(msg)
            resolved[correlation_id] = tuple(sorted(str(row[0]) for row in rows))
    finally:
        conn.close()
    return resolved


def _request(query: LabeledQuery) -> ConsultLoreRequest:
    # confidence=0.0 is the genuine vacuous state: it satisfies the
    # hypothesis-requires-confidence rule without expressing an opinion,
    # and the Interpreter never sees it.
    return ConsultLoreRequest(
        question=query.question,
        hypothesis=query.hypothesis,
        context=query.context,
        confidence=0.0 if query.hypothesis is not None else None,
    )


async def _search_ranked(
    *,
    hypotheses: HypothesisRepository,
    interpreted: InterpreterOutput,
    source_embeddings: list[list[float]],
    settings: LoreSettings,
) -> list[str]:
    candidates = await search_candidates(
        hypotheses=hypotheses,
        interpreted=interpreted,
        source_embeddings=source_embeddings,
        settings=settings,
    )
    # Multi-source pools merge without a global order; rank is the position
    # by composite score within the pool the Archivist would receive. A zero
    # score means no lane the weights admit matched at all: the pool is a
    # UNION of both lanes, so unmatched hypotheses ride in as filler and
    # their id-sort positions would read as ranks. Drop them; a "-" cell is
    # the honest record of a lane miss.
    ranked = sorted(candidates, key=lambda c: (-c.score, c.id))
    return [c.id for c in ranked if c.score > 0.0]


def _best_rank(hypothesis_ids: Sequence[str], *, results: Sequence[str]) -> int | None:
    ranks = [rank for h in hypothesis_ids if (rank := rank_of(h, results=results)) is not None]
    return min(ranks) if ranks else None


def collapse_outcomes(
    *,
    query: LabeledQuery,
    resolved: Mapping[str, tuple[str, ...]],
    composite: Sequence[str],
    proximity: Sequence[str],
    authority: Sequence[str],
) -> tuple[ExpectedEntry, ...]:
    # Rebuilds move content in both directions. Paraphrase collapse stores
    # several seeds as one hypothesis (the scen3 pair does): count it once,
    # under a joined label. A decomposition split stores one seed as several
    # hypotheses: score the seed by its best-ranked member. Grouping labels
    # by hypothesis set covers both without inflating the denominator.
    labels: dict[tuple[str, ...], list[str]] = {}
    for correlation_id in query.expected:
        labels.setdefault(resolved[correlation_id], []).append(correlation_id)
    return tuple(
        ExpectedEntry(
            correlation_id="+".join(correlation_ids),
            hypothesis_id="+".join(hypothesis_ids),
            composite=_best_rank(hypothesis_ids, results=composite),
            proximity=_best_rank(hypothesis_ids, results=proximity),
            authority=_best_rank(hypothesis_ids, results=authority),
        )
        for hypothesis_ids, correlation_ids in labels.items()
    )


async def _score_query(
    *,
    query: LabeledQuery,
    resolved: Mapping[str, tuple[str, ...]],
    providers: Providers,
    pool: RepositoryPool,
    settings: LoreSettings,
    variants: LaneVariants,
    t_now: int,
) -> tuple[QueryScore, ReceiptRow]:
    request = _request(query)
    # LLM calls stay outside the session scope (the retrieve module's
    # contract); the three search passes share one session. Read-only
    # throughout: no request row, no attestation, no hypothesis is written.
    interpreted = await interpret(
        providers=providers, request=request, settings=settings, t_now=t_now
    )
    # Mirrors Orchestrator.consult's fallback; a change there must land here
    # too, or the eval measures a different pipeline than production runs.
    question = interpreted.question or request.question or ""
    source_embeddings = await embed_sources(
        providers=providers, interpreted=interpreted, question=question
    )
    async with pool.session() as repos:
        composite = await _search_ranked(
            hypotheses=repos.hypotheses,
            interpreted=interpreted,
            source_embeddings=source_embeddings,
            settings=settings,
        )
        proximity = await _search_ranked(
            hypotheses=repos.hypotheses,
            interpreted=interpreted,
            source_embeddings=source_embeddings,
            settings=variants.proximity_only,
        )
        authority = await _search_ranked(
            hypotheses=repos.hypotheses,
            interpreted=interpreted,
            source_embeddings=source_embeddings,
            settings=variants.authority_only,
        )
    outcomes = collapse_outcomes(
        query=query,
        resolved=resolved,
        composite=composite,
        proximity=proximity,
        authority=authority,
    )
    row = ReceiptRow(
        query_id=query.id,
        keywords=tuple(interpreted.keywords),
        propositions=tuple(interpreted.propositions),
        expected=outcomes,
        composite_results=tuple(composite),
    )
    return QueryScore(query_id=query.id, outcomes=outcomes), row


def _append_row(log: Path, *, row: ReceiptRow) -> None:
    # Sync on purpose: each query's spend is receipted the moment it is
    # scored, so a crash mid-run keeps the receipts already paid for.
    with log.open("a", encoding="utf-8") as sink:
        sink.write(row.model_dump_json() + "\n")


async def _evaluate(*, prompt: Path | None, recall_log: Path) -> list[QueryScore]:
    from tests.e2e.corpus import SEEDS
    from tests.e2e.fixtures.golden import golden_copy
    from tests.e2e.queries import QUERIES

    # The db copy is scratch, not a receipt: unlike the artifacts dir, it
    # dies with the run.
    with tempfile.TemporaryDirectory(prefix="lore-recall-db-") as scratch:
        dsn = golden_copy(Path(scratch))
        resolved = resolve_expected(
            db_path=Path(dsn.removeprefix("sqlite:///")),
            oracles={seed.correlation_id: seed.oracle for seed in SEEDS},
            labeled={cid for query in QUERIES for cid in query.expected},
        )

        # Only so load_settings validates when no ambient DSN is configured;
        # the model_copy below overrides it either way. Everything else
        # ambient (a local lore.toml's retrieval weights) deliberately flows
        # in: the eval measures the config you'd run.
        os.environ.setdefault("DATABASE_URL", dsn)
        settings = load_settings().model_copy(update={"dsn": dsn})
        if prompt is not None:
            settings = with_interpreter_prompt(settings, prompt=prompt)
        variants = lane_variants(settings)

        # Mirrors lore.server.system minus sweep and orchestrator. check_health
        # stays: it fails a stale golden fixture loud before any spend.
        dim = resolve_dimensions(settings)
        run_migrations(settings=settings, embedding_dim=dim)
        check_health(settings=settings, embedding_dim=dim)
        pool = await connect(settings)
        try:
            providers = build_providers(settings)
            t_now = int(time.time())
            scores: list[QueryScore] = []
            for query in QUERIES:
                score, row = await _score_query(
                    query=query,
                    resolved=resolved,
                    providers=providers,
                    pool=pool,
                    settings=settings,
                    variants=variants,
                    t_now=t_now,
                )
                scores.append(score)
                _append_row(recall_log, row=row)
            return scores
        finally:
            await pool.close()


def main() -> None:
    # The whole driver is metered spend; a keyless run must exit here,
    # before any composition, filesystem work, or network.
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("recall: GEMINI_API_KEY not set; the eval drives live interpret and embed calls")
    # `python scripts/recall.py` puts scripts/ (not the repo root) on
    # sys.path, and the query labels and golden fixture live under tests/.
    # A no-op under pytest, which already resolves tests/ from the rootdir.
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    parser = argparse.ArgumentParser(
        description="Score two-lane retrieval recall over the labeled query set."
    )
    parser.add_argument("--artifacts", type=Path, default=None, help="persist the recall log here")
    parser.add_argument(
        "--prompt", type=Path, default=None, help="interpreter prompt override for delta runs"
    )
    args = parser.parse_args()
    # Every run is metered spend; the artifacts are the receipts. They always
    # persist, to a fresh tempdir unless --artifacts places them deliberately.
    if args.artifacts is None:
        root = Path(tempfile.mkdtemp(prefix="lore-recall-"))
    else:
        root = args.artifacts
        root.mkdir(parents=True, exist_ok=True)
    layout = artifact_layout(root)
    # One receipt per run: a rerun into the same --artifacts dir must
    # replace the old receipt, never concatenate two paid runs.
    layout.recall_log.unlink(missing_ok=True)
    scores = asyncio.run(_evaluate(prompt=args.prompt, recall_log=layout.recall_log))
    print(format_table(scores))
    print(f"recall@limit: {recall_at_limit(scores):.3f}")
    print(f"mrr: {mean_reciprocal_rank(scores):.3f}")
    print(f"artifacts: {root}")


if __name__ == "__main__":
    main()
