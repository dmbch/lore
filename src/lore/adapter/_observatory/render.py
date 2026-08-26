"""Frontier presentation: the prefab DataTable.

Pure functions, entries in and a view out: no I/O, no orchestrator. Swapping the
presentation never touches a tool, which is the point of the file boundary.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from prefab_ui.components import DataTable, DataTableColumn, ExpandableRow

from lore.domain import FrontierEntry


def _format_last_attested(last_attested: date | None) -> str:
    """ISO date, or ``never`` for the unattested (``None``)."""
    if last_attested is None:
        return "never"
    return last_attested.isoformat()


def render_frontier(entries: Sequence[FrontierEntry]) -> DataTable:
    """Build the frontier DataTable: one row per hypothesis, most uncertain first."""
    columns = [
        DataTableColumn(key="content", header="Hypothesis", sortable=True),
        DataTableColumn(
            key="c_herd", header="Herd", sortable=True, format="number:2", align="right"
        ),
        DataTableColumn(
            key="uncertainty",
            header="Uncertainty",
            sortable=True,
            format="number:2",
            align="right",
        ),
        DataTableColumn(
            key="oracles", header="Oracles", sortable=True, format="number", align="right"
        ),
        DataTableColumn(key="last_attested", header="Last attested", sortable=True, align="right"),
    ]
    # dict[str, Any] mirrors prefab's own cell-value type: cells are heterogeneous
    # (str, float, int) by construction and the renderer formats per column. The
    # ExpandableRow arm of the union goes unused; we render flat rows only.
    rows: list[dict[str, Any] | ExpandableRow] = [
        {
            "content": e.content,
            "c_herd": e.c_herd,
            "uncertainty": e.uncertainty,
            "oracles": e.oracle_count,
            "last_attested": _format_last_attested(e.last_attested),
        }
        for e in entries
    ]
    return DataTable(columns=columns, rows=rows, search=True, paginated=True)
