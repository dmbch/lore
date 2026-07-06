"""Orchestrator: the consult execution loop.

One method (``consult``) wires the five stages: interpret, retrieve,
reason, validate, record, each implemented in its own module.
"""

import asyncio
import random
import time
from typing import TYPE_CHECKING

import structlog

from lore.domain import (
    ConsultLoreRequest,
    ConsultLoreResponse,
    RetryableTransactionError,
    WriteContext,
)
from lore.math import MathService
from lore.repositories import RepositoryPool, RequestRecord
from lore.telemetry import start_span

from .interpret import interpret
from .reason import reason
from .record import record
from .retrieve import embed_novels, embed_sources, enrich, search_candidates
from .validate import validate_resolutions

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
        path = "write" if request.confidence is not None else "read"
        with start_span(
            "lore.consult",
            path=path,
            oracle_id=oracle_id,
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

            async with self._providers.session() as session:
                interpreted = await interpret(
                    session=session, request=request, settings=self._settings, t_now=t_now
                )
                log.info("consult.interpreted", propositions=len(interpreted.propositions))
                log.debug(
                    "consult.interpret.result",
                    question=interpreted.question,
                    propositions=interpreted.propositions,
                    keywords=interpreted.keywords,
                )

                question = interpreted.question or request.question or ""
                source_embeddings = await embed_sources(
                    session=session, interpreted=interpreted, question=question
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
                        t_now=t_now,
                    )

                log.info("consult.retrieved", candidates=len(candidates))
                log.debug(
                    "consult.enrich.result",
                    enriched=[
                        {
                            "id": e.id,
                            "c_herd": e.c_herd,
                            "attestation_count": e.attestation_count,
                            "score": e.score,
                        }
                        for e in enriched
                    ],
                )

                reasoned = await reason(
                    session=session,
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
                    novel_embeddings = await embed_novels(session=session, novels=novels)

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
                            log.warning(
                                "record.retry",
                                attempt=attempt + 1,
                                correlation_id=correlation_id,
                            )
                            ceiling = _RETRY_BASE_SECONDS * 2**attempt
                            delay = random.uniform(  # noqa: S311 - retry jitter, not cryptographic
                                ceiling / 2, ceiling
                            )
                            await asyncio.sleep(delay)

                return ConsultLoreResponse(answer=reasoned.answer)
