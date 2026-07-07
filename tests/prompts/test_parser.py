"""Tests for the sectioned-markdown parser."""

import pytest

from lore.prompts.parser import parse


def test_parse_empty_text_yields_empty_mapping() -> None:
    assert parse("") == {}


def test_parse_nests_by_heading_level() -> None:
    text = "# server\n## tools\n### consult\n#### description\ndesc body\n"
    assert parse(text) == {"server": {"tools": {"consult": {"description": "desc body"}}}}


def test_parse_siblings_share_a_parent() -> None:
    assert parse("# S\n## a\nbody a\n## b\nbody b\n") == {"S": {"a": "body a", "b": "body b"}}


def test_parse_leaf_section_maps_to_its_body() -> None:
    assert parse("# H\nbody text\n") == {"H": "body text"}


def test_parse_preserves_body_verbatim() -> None:
    # Internal blank lines and indentation are content, never reflowed.
    body = "line one\n\n    indented\nline three"
    assert parse(f"# H\n{body}\n") == {"H": body}


def test_parse_ignores_headings_inside_code_fences() -> None:
    mapping = parse("# H\nbefore\n```\n## not a heading\n```\nafter\n")
    assert list(mapping) == ["H"]
    assert "## not a heading" in str(mapping["H"])


def test_parse_strips_trailing_atx_closing_sequence() -> None:
    assert parse("## consult ##\nbody\n") == {"consult": "body"}


def test_parse_rejects_prose_beside_subsections() -> None:
    # Prose lives in leaves: a heading may hold text or subsections, not both.
    with pytest.raises(ValueError, match="mixes prose and subsections"):
        parse("# H\nstray prose\n## child\nbody\n")


def test_parse_rejects_duplicate_sibling_titles() -> None:
    with pytest.raises(ValueError, match="duplicate section title: 'a'"):
        parse("# S\n## a\nx\n## a\ny\n")
