"""Orchestrator: the consult execution loop.

``consult`` wires the five stages: interpret, retrieve, reason, validate,
record, each implemented in its own module. ``frontier`` is the read-only
observe path over the same session machinery.
"""

import asyncio
import random
import time
from typing import TYPE_CHECKING

import structlog
from pydantic import ValidationError

from lore.domain import (
    ConsultLoreRequest,
    ConsultLoreResponse,
    DomainInvariantError,
    FrontierEntry,
    RetryableTransactionError,
    WriteContext,
)
from lore.math import MathService
from lore.repositories import RepositoryPool, RequestRecord
from lore.telemetry import start_span

from ._interpret import interpret
from ._observe import FRONTIER_LIMIT
from ._observe import frontier as compute_frontier
from ._reason import reason
from ._record import record
from ._retrieve import embed_novels, embed_sources, enrich, search_candidates
from ._validate import validate_resolutions

if TYPE_CHECKING:
    from lore.config import LoreSettings
    from lore.providers import Providers

log = structlog.get_logger(__name__)

# Write-path retry policy. The PG SERIALIZABLE transaction may abort with
# SerializationFailure (SQLSTATE 40001) under concurrent writers on the
# same hypothesis; the pool translates that to RetryableTransactionError.
# Three attempts with equal-jitter exponential backoff cover the typical
# contention window without unbounded retries. The 20 ms base sits above a
# typical attestation transaction's completion time; the lower bound keeps
# us from retrying before the conflicting commit has cleared, and the
# jitter desynchronises simultaneous retriers so they do not collide again.
RECORD_MAX_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 0.02


class Orchestrator:
    def __init__(
        self,
        *,
        pool: RepositoryPool,
        providers: Providers,
        math: MathService,
        settings: LoreSettings,
    ) -> None:
        self._pool = pool
        self._providers = providers
        self._math = math
        self._settings = settings

    async def consult(
        self,
        *,
        oracle_id: str,
        request: ConsultLoreRequest,
        correlation_id: str,
    ) -> ConsultLoreResponse:
        """Run the consult execution loop.

        The request row is written autocommit before any downstream stage
        opens, so a failure in interpret/embed/reason/validate/record leaves
        provenance behind with no joining attestations: an orphan row by
        intent. See docs/architecture.md, "Orphan request rows are evidence,
        not garbage."
        """
        try:
            path = "write" if request.confidence is not None else "read"
            with start_span(
                "lore.consult",
                path=path,
                oracle_id=oracle_id,
                correlation_id=correlation_id,
            ):
                log.info("consult.start", path=path)

                t_now = int(time.time())

                async with self._pool.session() as repos:
                    await repos.requests.store(
                        RequestRecord(
                            id=correlation_id,
                            oracle_id=oracle_id,
                            timestamp=t_now,
                            question=request.question,
                            context=request.context,
                            hypothesis=request.hypothesis,
                            reasoning=request.reasoning,
                            confidence=request.confidence,
                        )
                    )

                interpreted = await interpret(
                    providers=self._providers, request=request, settings=self._settings, t_now=t_now
                )
                log.info("consult.interpreted", propositions=len(interpreted.propositions))
                log.debug(
                    "consult.interpret.result",
                    propositions=interpreted.propositions,
                    keywords=interpreted.keywords,
                )

                question = request.question or ""
                source_embeddings = await embed_sources(
                    providers=self._providers, interpreted=interpreted, question=question
                )

                async with self._pool.session() as repos:
                    candidates = await search_candidates(
                        hypotheses=repos.hypotheses,
                        interpreted=interpreted,
                        source_embeddings=source_embeddings,
                        settings=self._settings,
                    )
                    enriched = await enrich(
                        candidates=candidates,
                        attestations=repos.attestations,
                        math=self._math,
                        settings=self._settings,
                        t_now=t_now,
                    )

                log.info("consult.retrieved", candidates=len(candidates))
                log.debug(
                    "consult.enrich.result",
                    enriched=[
                        {
                            "id": e.id,
                            "c_herd": e.c_herd,
                            "oracle_count": e.oracle_count,
                            "score": e.score,
                        }
                        for e in enriched
                    ],
                )

                reasoned = await reason(
                    providers=self._providers,
                    request=request,
                    interpreted=interpreted,
                    enriched=enriched,
                    settings=self._settings,
                    t_now=t_now,
                )
                log.info("consult.reasoned", resolutions=len(reasoned.resolutions))
                if reasoned.notes:
                    log.info("consult.notes", count=len(reasoned.notes))
                    log.debug("consult.note_contents", notes=reasoned.notes)

                validate_resolutions(
                    reasoned=reasoned,
                    proposition_count=len(interpreted.propositions),
                    retrieved_ids=frozenset(e.id for e in enriched),
                )

                if request.confidence is not None:
                    novels = [
                        r.contributes for r in reasoned.resolutions if r.contributes is not None
                    ]
                    novel_embeddings = await embed_novels(providers=self._providers, novels=novels)

                    context = WriteContext(
                        oracle_id=oracle_id,
                        correlation_id=correlation_id,
                        confidence=request.confidence,
                        t_now=t_now,
                    )
                    for attempt in range(RECORD_MAX_ATTEMPTS):
                        try:
                            async with self._pool.transaction() as repos:
                                await record(
                                    repos=repos,
                                    math=self._math,
                                    reasoned=reasoned,
                                    novel_embeddings=novel_embeddings,
                                    context=context,
                                    settings=self._settings,
                                )
                            break
                        except RetryableTransactionError:
                            if attempt == RECORD_MAX_ATTEMPTS - 1:
                                raise
                            log.warning("record.retry", attempt=attempt + 1)
                            ceiling = _RETRY_BASE_SECONDS * 2**attempt
                            delay = random.uniform(  # noqa: S311 - jitter, not crypto
                                ceiling / 2, ceiling
                            )
                            await asyncio.sleep(delay)

                return ConsultLoreResponse(answer=reasoned.answer)
        except ValidationError as exc:
            # Internal models only: request validation happens in the adapter,
            # before consult. fastmcp re-raises raw pydantic errors to the
            # client, so an internal bug must be re-labeled to stay masked.
            msg = "internal domain model construction failed: a lore bug, not client input"
            raise DomainInvariantError(msg) from exc

    async def frontier(self, *, limit: int = FRONTIER_LIMIT) -> list[FrontierEntry]:
        """Return the current uncertainty frontier: newest hypotheses, most uncertain first.

        A read-only fan-out: opens an autocommit session, fetches and enriches
        the newest `limit` hypotheses, and delegates the fusion and ordering to
        the observe read path.
        """
        try:
            with start_span("lore.frontier", limit=limit):
                t_now = int(time.time())
                async with self._pool.session() as repos:
                    return await compute_frontier(
                        repos=repos,
                        math=self._math,
                        settings=self._settings,
                        limit=limit,
                        t_now=t_now,
                    )
        except ValidationError as exc:
            # Same masking contract as consult: FrontierEntry construction is
            # internal, never client input.
            msg = "internal domain model construction failed: a lore bug, not client input"
            raise DomainInvariantError(msg) from exc
