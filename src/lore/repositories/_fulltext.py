"""Shared full-text query builder for the authority lane.

Both backends consume the same phrase-OR syntax: SQLite FTS5 ``MATCH`` and
PostgreSQL ``websearch_to_tsquery`` each read ``"phrase" OR "phrase"``. One
builder keeps the two lanes from drifting.
"""

from collections.abc import Sequence


def build_fulltext_query(keywords: Sequence[str]) -> str:
    """Turn keywords into a phrase-OR full-text query.

    Each keyword becomes one double-quoted phrase, so its tokens match as an
    adjacency unit (phrase integrity); phrases combine with ``OR`` so any
    single keyword suffices (OR reachability). Internal double quotes become
    spaces: FTS5's doubling escape closes the phrase early under
    ``websearch_to_tsquery``, and neither tokenizer keeps the character
    anyway, so dropping it is the one treatment both backends read alike.

    Keywords left empty after quote removal and whitespace collapse are
    dropped. Returns ``""`` when no keyword survives, leaving the authority
    lane inert: SQLite skips the lane (FTS5 ``MATCH`` errors on an empty
    string), PostgreSQL matches nothing (``websearch_to_tsquery('')`` is
    empty).
    """
    phrases = [
        f'"{normalized}"'
        for keyword in keywords
        if (normalized := " ".join(keyword.replace('"', " ").split()))
    ]
    return " OR ".join(phrases)
