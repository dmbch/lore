"""Consistency checks for the labeled retrieval-recall query set."""

from tests.e2e.corpus import SEEDS
from tests.e2e.queries import QUERIES


def test_every_expected_id_names_a_corpus_seed() -> None:
    seed_ids = {seed.correlation_id for seed in SEEDS}

    for query in QUERIES:
        unknown = set(query.expected) - seed_ids
        assert not unknown, f"{query.id}: {sorted(unknown)}"


def test_query_ids_are_unique() -> None:
    ids = [query.id for query in QUERIES]

    assert len(ids) == len(set(ids))


def test_every_query_carries_text_and_expectations() -> None:
    for query in QUERIES:
        assert query.question or query.hypothesis, query.id
        assert query.expected, query.id


def test_expected_ids_are_unique_within_a_query() -> None:
    for query in QUERIES:
        assert len(query.expected) == len(set(query.expected)), query.id
