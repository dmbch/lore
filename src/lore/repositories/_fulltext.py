"""Shared full-text query builder for the authority lane.

Both backends consume the same phrase-OR syntax: SQLite FTS5 ``MATCH`` and
PostgreSQL ``websearch_to_tsquery`` each read ``"phrase" OR "phrase"``. One
builder keeps the two lanes from drifting.
"""

from collections.abc import Sequence

_DOUBLE_QUOTE = '"'


def build_fulltext_query(keywords: Sequence[str]) -> str:
    """Turn keywords into a phrase-OR full-text query.

    Each keyword becomes one double-quoted phrase, so its tokens match as an
    adjacency unit (phrase integrity); phrases combine with ``OR`` so any
    single keyword suffices (OR reachability). Internal double quotes are
    doubled, FTS5's escaping convention, which ``websearch_to_tsquery`` also
    tolerates.

    Empty or whitespace-only keywords are dropped. Returns ``""`` when no
    keyword survives, leaving the authority lane inert: SQLite skips the lane
    (FTS5 ``MATCH`` errors on an empty string), PostgreSQL matches nothing
    (``websearch_to_tsquery('')`` is empty).
    """
    phrases = [
        f'"{stripped.replace(_DOUBLE_QUOTE, _DOUBLE_QUOTE * 2)}"'
        for keyword in keywords
        if (stripped := keyword.strip())
    ]
    return " OR ".join(phrases)
