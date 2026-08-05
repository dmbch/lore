"""Retrieve stage: embed sources, search per-source, deduplicate, enrich with epistemic state.

Also hosts ``embed_novels``, the write-path counterpart of ``embed_sources``. Both
are pre-transaction LLM embedding calls keyed by ``task_type``; co-locating them
keeps the embedding surface in one file.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lore.domain import EvidenceInput, InterpreterOutput, SearchResult
from lore.math import MathService
from lore.repositories import AttestationsRepository, DecayWindow, HypothesisResult
from lore.telemetry import start_span

if TYPE_CHECKING:
    from lore.config import LoreSettings
    from lore.providers import Providers, TaskTypeKey
    from lore.repositories import HypothesisRepository


async def embed_sources(
    *,
    providers: Providers,
    interpreted: InterpreterOutput,
    question: str,
) -> list[list[float]]:
    """Embed the per-source texts. Pure LLM: must run outside any DB scope."""
    with start_span("lore.embed_sources"):
        # Questions are queries; propositions are facts to verify. Vendors that
        # distinguish task types (Gemini) need them tagged separately. One
        # batched request per task-type group cuts request count per consult.
        sources: list[tuple[str, TaskTypeKey]] = []
        if question and question.strip():
            sources.append((question, "question"))
        sources.extend((p, "verification") for p in interpreted.propositions if p and p.strip())

        groups: dict[TaskTypeKey, list[str]] = {}
        for text, key in sources:
            groups.setdefault(key, []).append(text)

        batches = await asyncio.gather(
            *(
                providers.embedder.embed_many(texts, task_type_key=key)
                for key, texts in groups.items()
            )
        )
        # Reassemble in source order: each source pulls the next vector from
        # its group's batch, which preserved per-group input order.
        pulls = {key: iter(batch) for key, batch in zip(groups, batches, strict=True)}
        return [next(pulls[key]) for _, key in sources]


async def embed_novels(
    *,
    providers: Providers,
    novels: list[str],
) -> dict[str, list[float]]:
    """Embed novel hypotheses for the write path. Pure LLM: must run outside the transaction."""
    with start_span("lore.embed_novels"):
        if not novels:
            return {}
        vectors = await providers.embedder.embed_many(novels, task_type_key="document")
        return dict(zip(novels, vectors, strict=True))


async def search_candidates(
    *,
    hypotheses: HypothesisRepository,
    interpreted: InterpreterOutput,
    source_embeddings: list[list[float]],
    settings: LoreSettings,
) -> list[HypothesisResult]:
    """Search per-source and dedup. Pure DB: runs inside a session scope."""
    with start_span("lore.search_candidates"):
        keywords = interpreted.keywords[: settings.retrieval.max_keywords]
        weights = settings.retrieval.weights
        limit = settings.retrieval.limit
        fan_out = settings.retrieval.fan_out

        # Sequential: psycopg's AsyncConnection is not safe across tasks,
        # and aiosqlite serializes anyway. The connection is bound to the
        # current session scope.
        result_sets: list[list[HypothesisResult]] = [
            await hypotheses.search(
                embedding=emb, keywords=keywords, weights=weights, limit=limit, fan_out=fan_out
            )
            for emb in source_embeddings
        ]

        return _deduplicate(result_sets)


async def enrich(
    *,
    candidates: list[HypothesisResult],
    attestations: AttestationsRepository,
    math: MathService,
    settings: LoreSettings,
    t_now: int,
) -> list[SearchResult]:
    with start_span("lore.enrich"):
        if not candidates:
            return []

        # Windowed fetch: rows older than 5 half-lives carry under ~3% of
        # their weight and stay unfetched. The view's aggregates remain
        # full-history, so count and last_attested stay exact.
        hypothesis_ids = [c.id for c in candidates]
        window = DecayWindow(t_now=t_now, half_life=settings.epistemics.attestation_half_life)
        attestation_map = await attestations.find_by_hypotheses(hypothesis_ids, window=window)

        enriched: list[SearchResult] = []
        for candidate in candidates:
            view = attestation_map[candidate.id]
            evidence = [
                EvidenceInput(c_oracle_discounted=a.c_oracle_discounted, timestamp=a.timestamp)
                for a in view.rows
            ]
            c_herd = (
                math.compute_confidence(attestations=evidence, t_now=t_now) if evidence else 0.0
            )
            last_attested = (
                datetime.fromtimestamp(view.last_attested, tz=UTC).date()
                if view.last_attested is not None
                else None
            )
            enriched.append(
                SearchResult(
                    id=candidate.id,
                    content=candidate.content,
                    c_herd=c_herd,
                    oracle_count=view.oracle_count,
                    last_attested=last_attested,
                    score=candidate.score,
                    # Engine cosine (pgvector, sqlite-vec) can overshoot the
                    # algebraic ±1 by float noise; SearchResult would reject it.
                    proximity=min(1.0, max(-1.0, candidate.proximity)),
                )
            )
        return enriched


def _deduplicate(result_sets: list[list[HypothesisResult]]) -> list[HypothesisResult]:
    """Merge search result sets, keeping the highest ``score`` per hypothesis."""
    best: dict[str, HypothesisResult] = {}
    for results in result_sets:
        for result in results:
            existing = best.get(result.id)
            if existing is None or result.score > existing.score:
                best[result.id] = result
    return list(best.values())
