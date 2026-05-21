"""Tests for FTS5 query sanitization."""

import pytest

from lore.repositories.sqlite.hypotheses import (
    _sanitize_fts5_query,  # pyright: ignore[reportPrivateUsage]
)


class TestSanitizeFts5Query:
    """_sanitize_fts5_query forces literal matching by quoting tokens."""

    def test_plain_tokens_are_quoted(self) -> None:
        assert _sanitize_fts5_query("hello world") == '"hello" "world"'

    def test_fts5_operators_are_neutralized(self) -> None:
        result = _sanitize_fts5_query("NOT failure OR success")
        assert result == '"NOT" "failure" "OR" "success"'

    def test_near_operator_is_neutralized(self) -> None:
        result = _sanitize_fts5_query("NEAR(a b)")
        assert result == '"NEAR(a" "b)"'

    def test_internal_double_quotes_are_escaped(self) -> None:
        result = _sanitize_fts5_query('say "hello" world')
        assert result == '"say" """hello""" "world"'

    def test_asterisk_wildcard_is_neutralized(self) -> None:
        result = _sanitize_fts5_query("post*")
        assert result == '"post*"'

    def test_caret_prefix_is_neutralized(self) -> None:
        result = _sanitize_fts5_query("^first")
        assert result == '"^first"'

    def test_single_token(self) -> None:
        assert _sanitize_fts5_query("hello") == '"hello"'

    def test_empty_string_returns_empty(self) -> None:
        assert _sanitize_fts5_query("") == ""

    @pytest.mark.parametrize("whitespace", [" ", "   ", "\t", "\n", " \t\n "])
    def test_whitespace_only_returns_empty(self, whitespace: str) -> None:
        assert _sanitize_fts5_query(whitespace) == ""

    def test_column_filter_syntax_is_neutralized(self) -> None:
        result = _sanitize_fts5_query("content:malicious")
        assert result == '"content:malicious"'
