"""Interpreter stage: decompose request into propositions and keywords."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lore.domain import ConsultLoreRequest, InterpreterInput, InterpreterOutput
from lore.prompts import build_core_prompt
from lore.telemetry import start_span

if TYPE_CHECKING:
    from lore.config import LoreSettings
    from lore.providers import Providers


async def interpret(
    *,
    providers: Providers,
    request: ConsultLoreRequest,
    settings: LoreSettings,
    t_now: int,
) -> InterpreterOutput:
    with start_span("lore.interpret"):
        return await providers.interpreter.complete(
            response_model=InterpreterOutput,
            system=build_core_prompt(settings.prompts, base=settings.prompts.interpreter),
            user=InterpreterInput(
                question=request.question,
                hypothesis=request.hypothesis,
                context=request.context,
                reasoning=request.reasoning,
                today=datetime.fromtimestamp(t_now, tz=UTC).date(),
            ).model_dump_json(),
        )
