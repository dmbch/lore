"""Retrieve stage: embed sources, search per-source, deduplicate, enrich with epistemic state.

Also hosts ``embed_novels``, the write-path counterpart of ``embed_sources``. Both
are pre-transaction LLM embedding calls keyed by ``task_type``; co-locating them
keeps the embedding surface in one file.
"""

import asyncio
from typing import TYPE_CHECKING

from lore.domain import EvidenceInput, InterpreterOutput, SearchResult
from lore.math import MathService
from lore.repositories import AttestationsRepository, HypothesisResult
from lore.telemetry import start_span

if TYPE_CHECKING:
    from lore.config import LoreSettings
    from lore.providers import Providers, TaskTypeKey
    from lore.repositories import HypothesisRepository


async def embed_sources(
    *,
    session: Providers,
    interpreted: InterpreterOutput,
    question: str,
) -> list[list[float]]:
    """Embed the per-source texts. Pure LLM: must run outside any DB scope."""
    with start_span("lore.embed_sources"):
        # Questions are queries; propositions are facts to verify. Vendors that
        # distinguish task types (Gemini) need them tagged separately.
        sources: list[tuple[str, TaskTypeKey]] = []
        if question and question.strip():
            sources.append((question, "question"))
        sources.extend((p, "verification") for p in interpreted.propositions if p and p.strip())

        return await asyncio.gather(
            *(session.embedder.embed(text, task_type_key=key) for text, key in sources)
        )


async def embed_novels(
    *,
    session: Providers,
    novels: list[str],
) -> dict[str, list[float]]:
    """Embed novel hypotheses for the write path. Pure LLM: must run outside the transaction."""
    with start_span("lore.embed_novels"):
        if not novels:
            return {}
        vectors: list[list[float]] = await asyncio.gather(
            *(session.embedder.embed(text, task_type_key="document") for text in novels)
        )
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
    t_now: int,
) -> list[SearchResult]:
    with start_span("lore.enrich"):
        if not candidates:
            return []

        hypothesis_ids = [c.id for c in candidates]
        attestation_map = await attestations.find_by_hypotheses(hypothesis_ids)

        enriched: list[SearchResult] = []
        for candidate in candidates:
            raw = attestation_map.get(candidate.id, [])
            evidence = [
                EvidenceInput(c_oracle_discounted=a.c_oracle_discounted, timestamp=a.timestamp)
                for a in raw
            ]
            c_herd = (
                math.compute_confidence(attestations=evidence, t_now=t_now) if evidence else 0.0
            )
            last_attested = max(a.timestamp for a in raw) if raw else 0
            enriched.append(
                SearchResult(
                    id=candidate.id,
                    content=candidate.content,
                    c_herd=c_herd,
                    attestation_count=len(raw),
                    last_attested=last_attested,
                    score=candidate.score,
                    proximity=candidate.proximity,
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
