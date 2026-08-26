"""Tests for Instructor-facing response model JSON schemas."""

from typing import Any

from lore.domain._types import (
    ArchivistOutput,
    InterpreterOutput,
)


def _assert_all_fields_described(schema: dict[str, Any]) -> None:
    """Assert every property in a JSON schema has a description."""
    properties: dict[str, Any] = schema.get("properties", {})
    for name, prop in properties.items():
        # For array fields with $ref items, check the referenced definition
        items: dict[str, Any] = prop.get("items", {})
        if "$ref" in items:
            ref_path = str(items["$ref"]).split("/")
            definition: dict[str, Any] = schema
            for part in ref_path:
                if part == "#":
                    continue
                definition = definition[part]
            _assert_all_fields_described(definition)
        assert prop.get("description"), f"field '{name}' is missing a description"


def test_interpreter_output_fields_have_descriptions() -> None:
    schema: dict[str, Any] = InterpreterOutput.model_json_schema()
    _assert_all_fields_described(schema)


def test_archivist_output_fields_have_descriptions() -> None:
    schema: dict[str, Any] = ArchivistOutput.model_json_schema()
    _assert_all_fields_described(schema)


def test_archivist_output_reasoning_precedes_answer() -> None:
    schema: dict[str, Any] = ArchivistOutput.model_json_schema()
    required: list[str] = schema["required"]
    reasoning_idx = required.index("reasoning")
    answer_idx = required.index("answer")
    assert reasoning_idx < answer_idx
