"""Reason stage: Archivist call."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from lore.domain import (
    ArchivistInput,
    ArchivistOutput,
    ConsultLoreRequest,
    InterpreterOutput,
    SearchResult,
)
from lore.prompts import build_core_prompt
from lore.telemetry import start_span

if TYPE_CHECKING:
    from lore.config import LoreSettings
    from lore.providers import Providers

log = structlog.get_logger(__name__)


async def reason(
    *,
    providers: Providers,
    request: ConsultLoreRequest,
    interpreted: InterpreterOutput,
    enriched: list[SearchResult],
    settings: LoreSettings,
    t_now: int,
) -> ArchivistOutput:
    with start_span("lore.reason"):
        reasoned = await providers.archivist.complete(
            response_model=ArchivistOutput,
            system=build_core_prompt(settings.prompts, base=settings.prompts.archivist),
            user=ArchivistInput(
                question=request.question,
                hypothesis=request.hypothesis,
                context=request.context,
                reasoning=request.reasoning,
                propositions=interpreted.propositions,
                retrieved=enriched,
                today=datetime.fromtimestamp(t_now, tz=UTC).date(),
            ).model_dump_json(),
        )
        log.debug(
            "consult.reason.result",
            reasoning=reasoned.reasoning,
            resolutions=[r.model_dump() for r in reasoned.resolutions],
        )
        return reasoned
