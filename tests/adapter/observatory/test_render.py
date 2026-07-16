"""Frontier render tests: the prefab DataTable (pure functions)."""

from datetime import date

from prefab_ui.components import DataTable

from lore.adapter.observatory.render import (
    _format_last_attested,  # pyright: ignore[reportPrivateUsage]
    render_frontier,
)
from lore.domain import FrontierEntry


def _entry(
    content: str = "a hypothesis",
    *,
    c_herd: float = 0.0,
    uncertainty: float = 1.0,
    attestation_count: int = 0,
    last_attested: date | None = None,
) -> FrontierEntry:
    return FrontierEntry(
        id="aaa00001-e29b-41d4-a716-446655440000",
        content=content,
        c_herd=c_herd,
        uncertainty=uncertainty,
        attestation_count=attestation_count,
        last_attested=last_attested,
    )


def test_render_frontier_builds_datatable_with_a_row_per_entry() -> None:
    entries = [_entry("h1", c_herd=0.2, uncertainty=0.8), _entry("h2", c_herd=0.9, uncertainty=0.1)]

    table = render_frontier(entries)

    assert isinstance(table, DataTable)
    assert [c.key for c in table.columns] == [
        "content",
        "c_herd",
        "uncertainty",
        "attestations",
        "last_attested",
    ]
    assert isinstance(table.rows, list)
    assert len(table.rows) == 2
    first = table.rows[0]
    assert isinstance(first, dict)
    assert first["content"] == "h1"
    assert first["uncertainty"] == 0.8


def test_render_frontier_empty_archive_builds_empty_table() -> None:
    table = render_frontier([])

    assert isinstance(table, DataTable)
    assert table.rows == []


def test_format_last_attested_is_never_for_unattested() -> None:
    assert _format_last_attested(None) == "never"


def test_format_last_attested_is_iso_date_for_attested() -> None:
    assert _format_last_attested(date(2033, 5, 18)) == "2033-05-18"
