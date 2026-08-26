"""Parse a sectioned markdown document into a nested, title-keyed mapping.

Splitting on ATX headings (``#``/``##``/``###``) by hand rather than pulling in
a markdown parser: bodies are handed to the model verbatim, so a line-level scan
keeps them byte-for-byte, where an AST round-trip would reflow whitespace and
escapes. Fenced code is tracked so a ``#`` inside a fence is body, not a heading.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

# ATX heading: 1-6 leading '#', a space, the title, an optional closing run of '#'.
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
# Fenced code delimiter: 3+ backticks or tildes, up to three leading spaces.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class _Section:
    """One ATX-heading section: its title, verbatim body, and nested subsections."""

    title: str
    body: str
    children: tuple[_Section, ...]


@dataclass
class _Node:
    level: int
    title: str
    body_lines: list[str]
    children: list[_Node]


def _freeze(node: _Node) -> _Section:
    return _Section(
        title=node.title,
        body="\n".join(node.body_lines).strip(),
        children=tuple(_freeze(child) for child in node.children),
    )


def _parse_sections(text: str) -> tuple[_Section, ...]:
    """Split ``text`` into a tree of sections, nested by heading depth.

    Content before the first heading is ignored. Headings inside fenced code
    blocks are treated as body text. Bodies keep their internal formatting
    verbatim; only surrounding blank lines are trimmed.
    """
    roots: list[_Node] = []
    stack: list[_Node] = []
    current: _Node | None = None
    in_fence = False
    fence_char = ""
    for line in text.splitlines():
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence, fence_char = True, marker
            elif marker == fence_char:
                in_fence = False
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            node = _Node(
                level=len(heading.group(1)),
                title=heading.group(2).strip(),
                body_lines=[],
                children=[],
            )
            while stack and stack[-1].level >= node.level:
                stack.pop()
            (stack[-1].children if stack else roots).append(node)
            stack.append(node)
            current = node
        elif current is not None:
            current.body_lines.append(line)
    return tuple(_freeze(root) for root in roots)


def _to_mapping(sections: Sequence[_Section]) -> dict[str, object]:
    mapping: dict[str, object] = {}
    for section in sections:
        if section.title in mapping:
            msg = f"duplicate section title: {section.title!r}"
            raise ValueError(msg)
        if section.body and section.children:
            msg = f"section {section.title!r} mixes prose and subsections"
            raise ValueError(msg)
        mapping[section.title] = _to_mapping(section.children) if section.children else section.body
    return mapping


def parse(text: str) -> dict[str, object]:
    """Parse a sectioned markdown document into a nested, title-keyed mapping.

    A leaf section (no subsections) maps to its body text; a branch section
    maps to the mapping of its children. Heading titles become mapping keys, so
    a document whose titles match a model's field names validates straight into
    that model. Two rules keep the mapping faithful: a section carrying both
    prose and subsections is rejected (the schema keeps prose in leaves), and
    duplicate sibling titles are rejected (the mapping would drop one silently).
    """
    return _to_mapping(_parse_sections(text))
