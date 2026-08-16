"""Tests for HypothesisRepository Protocol behavior."""

import math
import re
from collections.abc import Awaitable, Callable

import pytest

from lore.domain import StorageError
from lore.repositories.protocols import HypothesisRepository

# Must match the dimension used by session-scoped migrations in conftest.
_VECTOR_DIM: int = 1024


def _embedding(seed: int) -> list[float]:
    """Create a deterministic embedding with direction varying by seed.

    Cosine distance measures angle, not magnitude. Uniform vectors all
    point in the same direction. So we vary direction across dimensions.
    """
    return [math.sin(seed + i * 0.1) for i in range(_VECTOR_DIM)]


class TestStore:
    async def test_store_returns_record_with_generated_id(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        record = await hypothesis_repo.store(
            content="test claim", embedding=_embedding(1), created_at=1000
        )
        assert record.id  # non-empty UUID
        assert record.content == "test claim"
        assert record.created_at == 1000

    async def test_store_and_find_by_id(self, hypothesis_repo: HypothesisRepository) -> None:
        record = await hypothesis_repo.store(
            content="test claim", embedding=_embedding(1), created_at=1000
        )
        found = await hypothesis_repo.find_by_id(record.id)
        assert found == record

    async def test_store_same_content_creates_distinct_records(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        first = await hypothesis_repo.store(
            content="same claim", embedding=_embedding(1), created_at=1000
        )
        second = await hypothesis_repo.store(
            content="same claim", embedding=_embedding(2), created_at=2000
        )
        assert first.id != second.id

    async def test_store_rolls_back_on_vec_insert_failure(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        """Atomicity: neither table is modified if the vec insert fails."""
        wrong_dim = [1.0] * 10  # wrong dimension: backend rejects it
        with pytest.raises(StorageError):
            await hypothesis_repo.store(content="test claim", embedding=wrong_dim, created_at=1000)

        # A successful store after the failure must be the only record.
        # Verifies both vector and relational tables are clean: a leaked
        # relational row from the failed store would be an orphan (no vector
        # entry) invisible to search but visible to find_by_id.
        good = await hypothesis_repo.store(
            content="good claim", embedding=_embedding(1), created_at=2000
        )
        results = await hypothesis_repo.search(
            embedding=_embedding(1),
            keywords=["good claim"],
            weights=(1.0, 0.0),
            limit=10,
            fan_out=2,
        )
        assert len(results) == 1
        assert results[0].id == good.id


class TestStoreEmbeddingValidation:
    """store() rejects embeddings with no direction or non-finite components.

    Zero-magnitude vectors have undefined cosine direction (pgvector returns
    NaN, which COALESCE does not catch). NaN or infinite components corrupt
    similarity math. Both fail input validation before any database round-trip.
    """

    async def test_store_rejects_zero_magnitude_embedding(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        with pytest.raises(ValueError, match="non-zero magnitude"):
            await hypothesis_repo.store(
                content="all-zero embedding", embedding=[0.0] * _VECTOR_DIM, created_at=1000
            )

    async def test_store_rejects_nan_component_embedding(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        bad = _embedding(seed=1)
        bad[0] = math.nan
        with pytest.raises(ValueError, match="finite"):
            await hypothesis_repo.store(content="nan component", embedding=bad, created_at=1000)

    async def test_store_rejects_infinite_component_embedding(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        bad = _embedding(seed=1)
        bad[0] = math.inf
        with pytest.raises(ValueError, match="finite"):
            await hypothesis_repo.store(content="inf component", embedding=bad, created_at=1000)


class TestFindById:
    async def test_find_by_id_missing_returns_none(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        assert await hypothesis_repo.find_by_id("00000000-0000-0000-0000-000000000000") is None


class TestFindRecent:
    async def test_find_recent_returns_newest_first(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        for created_at in (1000, 2000, 3000):
            await hypothesis_repo.store(
                content=f"claim at {created_at}",
                embedding=_embedding(seed=created_at),
                created_at=created_at,
            )

        recent = await hypothesis_repo.find_recent(limit=10)

        assert [r.created_at for r in recent] == [3000, 2000, 1000]

    async def test_find_recent_bounded_by_limit(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        for created_at in (1000, 2000, 3000):
            await hypothesis_repo.store(
                content=f"claim at {created_at}",
                embedding=_embedding(seed=created_at),
                created_at=created_at,
            )

        recent = await hypothesis_repo.find_recent(limit=2)

        assert [r.created_at for r in recent] == [3000, 2000]

    async def test_find_recent_breaks_created_at_ties_by_id_ascending(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        stored = [
            await hypothesis_repo.store(
                content=f"tied claim {i}", embedding=_embedding(seed=i), created_at=5000
            )
            for i in range(3)
        ]

        recent = await hypothesis_repo.find_recent(limit=10)

        assert [r.id for r in recent] == sorted(r.id for r in stored)

    async def test_find_recent_rejects_non_positive_limit(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            await hypothesis_repo.find_recent(limit=0)


class TestSearch:
    async def test_search_empty_query_degrades_gracefully(
        self,
        hypothesis_repo: HypothesisRepository,
    ) -> None:
        """Empty query string degrades gracefully, results still returned via proximity."""
        await hypothesis_repo.store(
            content="redis cache invalidation strategy",
            embedding=_embedding(seed=5),
            created_at=1000,
        )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=5),
            keywords=[],
            weights=(1.0, 0.0),
            limit=10,
            fan_out=2,
        )

        assert len(results) > 0

    async def test_search_whitespace_only_query_degrades_gracefully(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        """Whitespace-only query is treated as empty: authority=0.0, no error."""
        await hypothesis_repo.store(
            content="redis cache invalidation strategy",
            embedding=_embedding(seed=5),
            created_at=1000,
        )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=5),
            keywords=["   \t  "],
            weights=(1.0, 0.0),
            limit=10,
            fan_out=2,
        )

        assert len(results) > 0

    async def test_search_empty_table_returns_empty(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        embedding = _embedding(seed=1)
        results = await hypothesis_repo.search(
            embedding=embedding,
            keywords=["any query"],
            weights=(0.5, 0.5),
            limit=10,
            fan_out=2,
        )
        assert results == []

    async def test_search_finds_hypothesis_matching_both_lanes(
        self,
        hypothesis_repo: HypothesisRepository,
    ) -> None:
        # Arrange: store two hypotheses with distinct content and embeddings
        h1 = await hypothesis_repo.store(
            content="gRPC migration reduced latency by forty percent",
            embedding=_embedding(seed=1),
            created_at=1000,
        )
        await hypothesis_repo.store(
            content="kafka consumer group rebalancing causes timeouts",
            embedding=_embedding(seed=2),
            created_at=2000,
        )

        # Act: search with embedding close to h1 and query matching h1
        results = await hypothesis_repo.search(
            embedding=_embedding(seed=1),
            keywords=["gRPC", "migration", "latency"],
            weights=(0.5, 0.5),
            limit=10,
            fan_out=2,
        )

        # Assert: h1 ranks first, multi-lane convergence outscores single-lane presence
        assert len(results) >= 2
        assert results[0].id == h1.id

    async def test_search_vector_only_candidates(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        """A hypothesis found by embedding proximity but not FTS still appears."""
        await hypothesis_repo.store(
            content="alpha beta gamma",
            embedding=_embedding(seed=42),
            created_at=1000,
        )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=42),
            keywords=["completely unrelated terms"],
            weights=(1.0, 0.0),
            limit=10,
            fan_out=2,
        )

        assert len(results) == 1
        assert results[0].content == "alpha beta gamma"

    async def test_search_weights_configurable(
        self,
        hypothesis_repo: HypothesisRepository,
    ) -> None:
        h1 = await hypothesis_repo.store(
            content="kubernetes deployment orchestration",
            embedding=_embedding(seed=1),
            created_at=1000,
        )
        h2 = await hypothesis_repo.store(
            content="general purpose notes",
            embedding=_embedding(seed=2),
            created_at=2000,
        )

        # Authority-only search: h1 should rank first (FTS match on content)
        authority_results = await hypothesis_repo.search(
            embedding=_embedding(seed=50),
            keywords=["kubernetes deployment"],
            weights=(0.0, 1.0),
            limit=10,
            fan_out=2,
        )

        # Proximity-only search: seed=50 is closer to seed=2 than seed=1
        proximity_results = await hypothesis_repo.search(
            embedding=_embedding(seed=2),
            keywords=["kubernetes deployment"],
            weights=(1.0, 0.0),
            limit=10,
            fan_out=2,
        )

        # Assert: both searches return results
        assert len(authority_results) >= 1
        assert len(proximity_results) >= 1

        # Authority finds h1 first (FTS match), proximity finds h2 first (closer
        # embedding). Both halves are asserted: one alone passes when the
        # weights are ignored and one ordering happens to dominate.
        assert authority_results[0].id == h1.id
        assert proximity_results[0].id == h2.id

    async def test_search_fts_only_candidates(self, hypothesis_repo: HypothesisRepository) -> None:
        """A hypothesis found by FTS keyword match ranks first when authority is the sole signal."""
        await hypothesis_repo.store(
            content="PostgreSQL migration failed on Tuesday",
            embedding=_embedding(seed=1),
            created_at=1000,
        )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=999),
            keywords=["PostgreSQL migration"],
            weights=(0.0, 1.0),
            limit=10,
            fan_out=2,
        )

        assert len(results) >= 1
        assert results[0].content == "PostgreSQL migration failed on Tuesday"

    async def test_search_single_matching_keyword_suffices(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        """OR reachability: one matching keyword surfaces the row, even among misses.

        The authority lane combines keywords with OR, so a single hit is
        enough. Under keyword-AND every added keyword would narrow the lane
        and the two non-matching keywords would shut the row out.
        """
        stored = await hypothesis_repo.store(
            content="quantum entanglement experiment succeeded",
            embedding=_embedding(seed=7),
            created_at=1000,
        )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=500),
            keywords=["quantum", "unrelated", "nonsense"],
            weights=(0.0, 1.0),
            limit=10,
            fan_out=2,
        )

        # score > 0 with the proximity weight zeroed isolates authority-lane
        # membership: the row surfaced because a keyword matched, not because
        # the UNION pool carried it in from the proximity lane.
        matched = {r.id for r in results if r.score > 0}
        assert stored.id in matched

    async def test_search_multi_token_keyword_matches_as_phrase(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        """Phrase integrity: a multi-token keyword matches an adjacency, not token-OR.

        ``"content delivery network"`` must match a document carrying that
        phrase and miss one that carries the same three tokens scattered.
        Under token-level AND/OR both would match, collapsing the distinction.
        """
        phrase_doc = await hypothesis_repo.store(
            content="the content delivery network cached the asset",
            embedding=_embedding(seed=8),
            created_at=1000,
        )
        scattered = await hypothesis_repo.store(
            content="content flows through a delivery pipe on our network",
            embedding=_embedding(seed=9),
            created_at=2000,
        )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=500),
            keywords=["content delivery network"],
            weights=(0.0, 1.0),
            limit=10,
            fan_out=2,
        )

        matched = {r.id for r in results if r.score > 0}
        assert phrase_doc.id in matched
        assert scattered.id not in matched

    async def test_search_more_keyword_matches_rank_higher(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        """A hypothesis matching more keywords ranks above one matching fewer.

        The keywords are rarity-comparable: each appears in exactly one stored
        hypothesis, so their FTS5/ts_rank term weights are equal and the
        ordering isolates match count from term-rarity skew. Certifies the
        ranking the RRF handoff relies on without an explicit match count.
        """
        three = await hypothesis_repo.store(
            content="alfa bravo charlie",
            embedding=_embedding(seed=1),
            created_at=1000,
        )
        one = await hypothesis_repo.store(
            content="delta",
            embedding=_embedding(seed=2),
            created_at=2000,
        )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=500),
            keywords=["alfa", "bravo", "charlie", "delta"],
            weights=(0.0, 1.0),
            limit=10,
            fan_out=2,
        )

        order = [r.id for r in results if r.score > 0]
        assert three.id in order
        assert one.id in order
        assert order.index(three.id) < order.index(one.id)

    async def test_search_respects_limit(self, hypothesis_repo: HypothesisRepository) -> None:
        for seed in range(1, 6):
            await hypothesis_repo.store(
                content=f"hypothesis number {seed} about distinct topic {seed}",
                embedding=_embedding(seed=seed),
                created_at=1000 * seed,
            )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=1),
            keywords=["hypothesis"],
            weights=(0.5, 0.5),
            limit=2,
            fan_out=2,
        )

        assert len(results) == 2

    async def test_search_proximity_and_authority_both_contribute(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        await hypothesis_repo.store(
            content="PostgreSQL vacuum analysis performance",
            embedding=_embedding(seed=10),
            created_at=1000,
        )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=10),
            keywords=["PostgreSQL vacuum"],
            weights=(0.5, 0.5),
            limit=10,
            fan_out=2,
        )

        assert len(results) >= 1
        matched = [r for r in results if "vacuum" in r.content]
        assert len(matched) == 1

    async def test_search_returns_hypothesis_results_with_scores(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        await hypothesis_repo.store(
            content="gRPC migration reduced latency",
            embedding=_embedding(seed=1),
            created_at=1000,
        )
        results = await hypothesis_repo.search(
            embedding=_embedding(seed=1),
            keywords=["gRPC", "migration", "latency"],
            weights=(0.5, 0.5),
            limit=10,
            fan_out=2,
        )
        assert len(results) >= 1
        r = results[0]
        assert 0.0 <= r.score <= 1.0
        # Cosine proximity is in [-1, 1]; randomized embeddings can land
        # anywhere within the range.
        assert -1.0 <= r.proximity <= 1.0

    async def test_search_proximity_only_has_positive_score(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        await hypothesis_repo.store(
            content="alpha beta gamma",
            embedding=_embedding(seed=42),
            created_at=1000,
        )
        results = await hypothesis_repo.search(
            embedding=_embedding(seed=42),
            keywords=["completely unrelated terms"],
            weights=(1.0, 0.0),
            limit=10,
            fan_out=2,
        )
        assert len(results) == 1
        assert results[0].score > 0.0

    async def test_search_rrf_rank_determines_score(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        """RRF scores reflect rank position: rank #1 scores higher than rank #2."""
        await hypothesis_repo.store(
            content="closest hypothesis to the query embedding",
            embedding=_embedding(seed=10),
            created_at=1000,
        )
        await hypothesis_repo.store(
            content="farther hypothesis from the query embedding",
            embedding=_embedding(seed=99),
            created_at=2000,
        )

        results = await hypothesis_repo.search(
            embedding=_embedding(seed=10),
            keywords=[],
            weights=(1.0, 0.0),
            limit=10,
            fan_out=2,
        )

        assert len(results) == 2
        assert results[0].content == "closest hypothesis to the query embedding"
        assert results[1].content == "farther hypothesis from the query embedding"

    async def test_search_fan_out_controls_candidate_pool_size(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        """``fan_out`` sets the per-lane LIMIT inside each subquery.

        With ``limit=1, fan_out=1`` each lane fetches its top-1 row only.
        Hypothesis C (proximity rank 2, authority rank 2) never enters
        either lane, so it cannot win.

        With ``limit=1, fan_out=3`` each lane fetches its top-3. C now
        appears in both lanes (rank 2 in each), and its cross-lane
        composite ``2 * 0.5/62`` overtakes the single-lane single-rank
        winners F1 and B (each ``0.5/61``).
        """
        # Embeddings sized for SCHEMA_DIM=1024 (must match conftest schema).
        # The query direction is e1; rows are placed at decreasing
        # alignments with it so proximity ranks are deterministic across
        # backends.
        dim = _VECTOR_DIM
        q_emb = [1.0] + [0.0] * (dim - 1)
        f1_emb = [1.0] + [0.0] * (dim - 1)  # cos = 1.0 → prox rank 1
        c_emb = [1.0, 0.1] + [0.0] * (dim - 2)  # cos ≈ 0.995 → prox rank 2
        f2_emb = [1.0, 0.2] + [0.0] * (dim - 2)  # cos ≈ 0.981 → prox rank 3
        b_emb = [0.01, 1.0] + [0.0] * (dim - 2)  # cos ≈ 0.01 → prox rank ≥ 4

        # FTS: only B and C carry the rare keyword. B repeats it twice and
        # C once, both BM25 (SQLite) and ts_rank (Postgres) reward higher
        # term frequency, so B is unambiguously auth rank 1 and C auth rank 2.
        await hypothesis_repo.store(content="filler one alpha", embedding=f1_emb, created_at=1000)
        c = await hypothesis_repo.store(
            content="rarewidget charlie content", embedding=c_emb, created_at=1001
        )
        await hypothesis_repo.store(content="filler two delta", embedding=f2_emb, created_at=1002)
        await hypothesis_repo.store(
            content="rarewidget rarewidget bravo bravo", embedding=b_emb, created_at=1003
        )

        narrow = await hypothesis_repo.search(
            embedding=q_emb,
            keywords=["rarewidget"],
            weights=(0.5, 0.5),
            limit=1,
            fan_out=1,
        )
        wide = await hypothesis_repo.search(
            embedding=q_emb,
            keywords=["rarewidget"],
            weights=(0.5, 0.5),
            limit=1,
            fan_out=3,
        )

        narrow_ids = {r.id for r in narrow}
        wide_ids = {r.id for r in wide}

        # The discriminator: C is the cross-lane winner that surfaces only
        # when fan_out widens both lanes' subqueries to include rank-2 rows.
        assert c.id not in narrow_ids, (
            f"fan_out=1 should not surface C (prox/auth rank 2); got {narrow_ids}"
        )
        assert c.id in wide_ids, (
            f"fan_out=3 should surface C as the cross-lane top-1; got {wide_ids}"
        )

    @pytest.mark.parametrize("bad_limit", [0, -1])
    async def test_search_invalid_limit_raises(
        self, hypothesis_repo: HypothesisRepository, bad_limit: int
    ) -> None:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            await hypothesis_repo.search(
                embedding=_embedding(seed=1),
                keywords=["any"],
                weights=(0.5, 0.5),
                limit=bad_limit,
                fan_out=2,
            )

    @pytest.mark.parametrize("bad_fan_out", [0, -1])
    async def test_search_invalid_fan_out_raises(
        self, hypothesis_repo: HypothesisRepository, bad_fan_out: int
    ) -> None:
        with pytest.raises(ValueError, match="fan_out must be >= 1"):
            await hypothesis_repo.search(
                embedding=_embedding(seed=1),
                keywords=["any"],
                weights=(0.5, 0.5),
                limit=5,
                fan_out=bad_fan_out,
            )

    async def test_search_weights_must_sum_to_one(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        with pytest.raises(ValueError, match=re.escape("weights must sum to 1.0")):
            await hypothesis_repo.search(
                embedding=_embedding(seed=1),
                keywords=["any"],
                weights=(1.0, 1.0),
                limit=5,
                fan_out=2,
            )

    async def test_search_negative_weight_raises(
        self, hypothesis_repo: HypothesisRepository
    ) -> None:
        """Negative weights are rejected: they would invert a lane's ranking signal."""
        with pytest.raises(ValueError, match="weights must be non-negative"):
            await hypothesis_repo.search(
                embedding=_embedding(seed=1),
                keywords=["any"],
                weights=(-0.5, 1.5),
                limit=5,
                fan_out=2,
            )


class TestStorageError:
    async def test_store_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        hypothesis_repo: HypothesisRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await hypothesis_repo.store(content="claim", embedding=_embedding(1), created_at=1000)

    async def test_find_by_id_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        hypothesis_repo: HypothesisRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await hypothesis_repo.find_by_id("00000000-0000-0000-0000-000000000000")

    async def test_find_recent_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        hypothesis_repo: HypothesisRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await hypothesis_repo.find_recent(limit=10)

    async def test_search_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        hypothesis_repo: HypothesisRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await hypothesis_repo.search(
                embedding=_embedding(0),
                keywords=["any query"],
                weights=(0.5, 0.5),
                limit=5,
                fan_out=2,
            )
