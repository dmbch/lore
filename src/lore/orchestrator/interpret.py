"""Interpreter stage — decompose request into propositions and keywords."""

from typing import TYPE_CHECKING

from lore.domain import ConsultLoreRequest, InterpreterInput, InterpreterOutput
from lore.prompts import load_prompt
from lore.telemetry import start_span

if TYPE_CHECKING:
    from lore.config import LoreSettings
    from lore.providers import Providers


async def interpret(
    *,
    session: Providers,
    request: ConsultLoreRequest,
    settings: LoreSettings,
) -> InterpreterOutput:
    with start_span("lore.interpret"):
        return await session.interpreter.complete(
            response_model=InterpreterOutput,
            system=load_prompt(settings.prompts.interpreter),
            user=InterpreterInput(
                question=request.question,
                hypothesis=request.hypothesis,
                context=request.context,
                reasoning=request.reasoning,
            ).model_dump_json(),
        )
