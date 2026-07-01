"""Reason stage: Archivist call."""

from typing import TYPE_CHECKING

import structlog

from lore.domain import (
    ArchivistInput,
    ArchivistOutput,
    ConsultLoreRequest,
    InterpreterOutput,
    SearchResult,
)
from lore.prompts import load_prompt
from lore.telemetry import start_span

if TYPE_CHECKING:
    from lore.config import LoreSettings
    from lore.providers import Providers

log = structlog.get_logger(__name__)


async def reason(
    *,
    session: Providers,
    request: ConsultLoreRequest,
    interpreted: InterpreterOutput,
    enriched: list[SearchResult],
    settings: LoreSettings,
) -> ArchivistOutput:
    with start_span("lore.reason"):
        reasoned = await session.archivist.complete(
            response_model=ArchivistOutput,
            system=load_prompt(settings.prompts.archivist),
            user=ArchivistInput(
                question=request.question,
                hypothesis=request.hypothesis,
                context=request.context,
                reasoning=request.reasoning,
                propositions=interpreted.propositions,
                retrieved=enriched,
            ).model_dump_json(),
        )
        log.debug(
            "consult.reason.result",
            reasoning=reasoned.reasoning,
            resolutions=[r.model_dump() for r in reasoned.resolutions],
        )
        return reasoned
