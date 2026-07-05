"""Tests for the shared phrase-OR full-text query builder."""

from lore.repositories._fulltext import build_fulltext_query


class TestBuildFulltextQuery:
    """``build_fulltext_query`` turns keywords into a phrase-OR query.

    Each keyword becomes one double-quoted phrase (its tokens match as an
    adjacency unit); phrases combine with OR so any single keyword suffices.
    Both FTS5 ``MATCH`` and ``websearch_to_tsquery`` consume this syntax.
    """

    def test_build_fulltext_query_or_joins_quoted_phrases(self) -> None:
        assert build_fulltext_query(["a b", "c"]) == '"a b" OR "c"'

    def test_single_keyword_is_one_quoted_phrase(self) -> None:
        assert build_fulltext_query(["hello world"]) == '"hello world"'

    def test_single_token_keyword_is_quoted(self) -> None:
        assert build_fulltext_query(["hello"]) == '"hello"'

    def test_empty_list_yields_empty_query(self) -> None:
        assert build_fulltext_query([]) == ""

    def test_empty_and_whitespace_keywords_are_dropped(self) -> None:
        assert build_fulltext_query(["", "  \t ", "kept"]) == '"kept"'

    def test_all_empty_keywords_yield_empty_query(self) -> None:
        assert build_fulltext_query(["", "   "]) == ""

    def test_keyword_whitespace_is_trimmed(self) -> None:
        assert build_fulltext_query(["  spaced  "]) == '"spaced"'

    def test_internal_double_quotes_become_spaces(self) -> None:
        # FTS5's doubling escape closes the phrase early under
        # websearch_to_tsquery, and neither tokenizer keeps the quote anyway,
        # so the builder drops the character instead of escaping it.
        assert build_fulltext_query(['say "hi"']) == '"say hi"'

    def test_quote_only_keyword_is_dropped(self) -> None:
        assert build_fulltext_query(['"']) == ""

    def test_operators_inside_a_keyword_stay_literal(self) -> None:
        # Quoting forces literal matching: NOT/OR/NEAR carry no operator meaning
        # inside a phrase.
        assert build_fulltext_query(["NOT failure OR success"]) == '"NOT failure OR success"'
