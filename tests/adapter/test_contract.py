"""Tests for the MCP server contract loader."""

import importlib.resources
from pathlib import Path

import pytest

from lore.adapter._contract import (  # pyright: ignore[reportPrivateUsage]
    CONSULT_TOOL,
    load_server_contract,
)

# The consult tool's parameters, for building fixture markdown. The contract
# models enforce this set; the tests only need the names to render sections.
_FIELDS = ("question", "context", "hypothesis", "reasoning", "confidence")


def _fields_block(names: tuple[str, ...]) -> str:
    fields = "".join(f"##### {name}\n{name} description\n" for name in names)
    return f"#### fields\n{fields}"


def _valid(instructions: str = "server instructions", *, fields: tuple[str, ...] = _FIELDS) -> str:
    return (
        f"# Lore\n"
        f"## instructions\n{instructions}\n"
        f"## tools\n"
        f"### {CONSULT_TOOL}\n"
        f"#### description\ntool description\n"
        f"{_fields_block(fields)}"
    )


def _bundled_contract() -> Path:
    return Path(str(importlib.resources.files("lore.prompts").joinpath("contract.md")))


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "contract.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_server_contract_builds_nested_contract(tmp_path: Path) -> None:
    contract = load_server_contract(_write(tmp_path, _valid()))
    assert contract.instructions == "server instructions"
    tool = contract.tools.consult
    assert tool.description == "tool description"
    assert tool.fields.question == "question description"


def test_load_server_contract_rejects_multiple_servers(tmp_path: Path) -> None:
    text = _valid() + "# Second\n## instructions\nx\n"
    with pytest.raises(ValueError, match="exactly one server"):
        load_server_contract(_write(tmp_path, text))


def test_load_server_contract_rejects_unknown_server_section(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"[Ee]xtra"):
        load_server_contract(_write(tmp_path, _valid() + "## notes\nstray\n"))


def test_load_server_contract_rejects_missing_consult_tool(tmp_path: Path) -> None:
    text = (
        "# Lore\n## instructions\ni\n## tools\n"
        "### other\n#### description\nd\n#### fields\n##### x\ny\n"
    )
    with pytest.raises(ValueError, match=CONSULT_TOOL):
        load_server_contract(_write(tmp_path, text))


def test_load_server_contract_rejects_missing_field(tmp_path: Path) -> None:
    fields = tuple(name for name in _FIELDS if name != "confidence")
    with pytest.raises(ValueError, match="confidence"):
        load_server_contract(_write(tmp_path, _valid(fields=fields)))


def test_load_server_contract_rejects_unknown_field(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bogus"):
        load_server_contract(_write(tmp_path, _valid() + "##### bogus\nx\n"))


def test_load_server_contract_rejects_duplicate_field(tmp_path: Path) -> None:
    text = (
        f"# Lore\n## instructions\ni\n## tools\n### {CONSULT_TOOL}\n"
        f"#### description\nd\n#### fields\n##### question\na\n##### question\nb\n"
    )
    with pytest.raises(ValueError, match="duplicate section title: 'question'"):
        load_server_contract(_write(tmp_path, text))


def test_load_server_contract_rejects_duplicate_tool(tmp_path: Path) -> None:
    text = _valid() + f"### {CONSULT_TOOL}\n#### description\nsecond\n{_fields_block(_FIELDS)}"
    with pytest.raises(ValueError, match="duplicate section title: 'consult'"):
        load_server_contract(_write(tmp_path, text))


def test_bundled_contract_loads() -> None:
    contract = load_server_contract(_bundled_contract())
    assert contract.tools.consult.fields.question.strip() != ""
    assert contract.instructions.strip() != ""
