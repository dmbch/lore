"""The MCP server contract: server instructions, tool description, field docs.

Parsed from a sectioned markdown file (``prompts/contract.md``) whose headings
mirror MCP's object model: a ``#`` server holds a ``## tools`` collection of
``###`` tools, each holding a ``#### description`` and a ``#### fields``
collection of ``#####`` fields. Heading titles are these models' field names,
so the section mapping validates straight in. The shape is fixed (one tool,
``consult``, with exactly five fields), so it is spelled out as types rather
than re-checked at runtime: ``extra="forbid"`` plus required fields makes a
drifted or malformed contract fail loud at server construction.

``ServerContract`` is the one public model. The sub-models are private: a
consumer holds the typed root and reaches its parts by attribute, e.g.
``contract.tools.consult.fields.question``.
"""

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lore.prompts import load_contract

# The client-facing name shared by the consult tool and the consult prompt.
CONSULT_TOOL = "consult"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _ConsultFields(_Strict):
    """The consult tool's five parameter descriptions."""

    question: str
    context: str
    hypothesis: str
    reasoning: str
    confidence: str


class _ConsultTool(_Strict):
    """The consult tool's client-facing docs: its description and field docs."""

    description: str
    fields: _ConsultFields


class _Tools(_Strict):
    """The server's tool set: exactly one tool, consult."""

    consult: _ConsultTool


class ServerContract(_Strict):
    """The server's client-facing docs: ambient instructions and its tools."""

    instructions: str
    tools: _Tools


def load_server_contract(path: Path) -> ServerContract:
    """Load and validate the MCP contract, or raise ``ValueError`` if malformed."""
    return load_contract(path, build=_build_contract)


def _build_contract(bag: Mapping[str, object]) -> ServerContract:
    if len(bag) != 1:
        msg = f"contract needs exactly one server section, found {len(bag)}"
        raise ValueError(msg)
    (server,) = bag.values()
    return ServerContract.model_validate(server)
