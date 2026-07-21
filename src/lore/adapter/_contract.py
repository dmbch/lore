"""The MCP server contract: server instructions, tool description, field docs.

Parsed from a sectioned markdown file (``prompts/contract.md``) whose headings
mirror MCP's object model: a ``#`` server holds a ``## tools`` collection of
``###`` tools. The ``consult`` tool holds a ``#### description`` and a ``####
fields`` collection of ``#####`` fields; the ``observe`` UI tool holds a
``#### description`` alone. Heading titles are these models' field names, so the
section mapping validates straight in. The shape is fixed (two tools, ``consult``
with exactly five fields and ``observe`` with a description alone), so it is
spelled out as types rather than re-checked at runtime: ``extra="forbid"`` plus
required fields makes a drifted or malformed contract fail loud at server
construction.

``ServerContract`` is the one public model. The sub-models are private: a
consumer holds the typed root and reaches its parts by attribute, e.g.
``contract.tools.consult.fields.question`` or ``contract.tools.observe.description``.
"""

from collections.abc import Mapping
from pathlib import Path

from lore._pydantic import ConfigModel
from lore.prompts import load_contract

# The client-facing name shared by the consult tool and the consult prompt.
CONSULT_TOOL = "consult"


class _ConsultFields(ConfigModel):
    """The consult tool's five parameter descriptions."""

    question: str
    context: str
    hypothesis: str
    reasoning: str
    confidence: str


class _ConsultTool(ConfigModel):
    """The consult tool's client-facing docs: its description and field docs."""

    description: str
    fields: _ConsultFields


class _ObserveTool(ConfigModel):
    """The observe UI tool's client-facing docs: a description, no params."""

    description: str


class _Tools(ConfigModel):
    """The server's tool set: consult (the write path) and observe (the UI)."""

    consult: _ConsultTool
    observe: _ObserveTool


class ServerContract(ConfigModel):
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
