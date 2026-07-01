"""SQLite implementation of HypothesisRepository."""

import sqlite3
from collections.abc import Sequence

import aiosqlite
import sqlite_vec

from lore.domain import StorageError
from lore.repositories import records
from lore.repositories._validation import validate_embedding, validate_search_params
from lore.repositories.records import HypothesisRecord, HypothesisResult
from lore.repositories.sqlite._errors import classify_integrity_error


def _sanitize_fts5_query(query: str) -> str:
    """Force literal matching by double-quoting each token.

    FTS5 interprets query syntax (NOT, OR, AND, NEAR, +, *, ^, etc.).
    PostgreSQL's plainto_tsquery strips all operators by design — to
    maintain parity, we quote every token so FTS5 treats them as literals.

    Internal double quotes are escaped by doubling (FTS5's convention,
    same as SQL/CSV — not backslash escaping like JSON). No stdlib
    function provides FTS5 quoting.
    """
    tokens = query.split()
    double_quote = '"'
    return " ".join(f'"{t.replace(double_quote, double_quote * 2)}"' for t in tokens if t)


class SqliteHypothesisRepository:
    """Hypothesis storage backed by SQLite + sqlite-vec."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def store(
        self, *, content: str, embedding: Sequence[float], created_at: int
    ) -> HypothesisRecord:
        """Create a hypothesis with a generated UUIDv4 and persist it.

        Three tables (hypotheses + vec_hypotheses + fts_hypotheses) is a
        SQLite idiosyncrasy — sqlite-vec and FTS5 each require separate
        virtual tables. Must run inside ``pool.transaction()``: atomicity
        across the three inserts is provided by the outer transaction,
        not by an inner SAVEPOINT. Under ``pool.session()`` (autocommit)
        a failure on the vec or fts insert would leave the relational
        row orphaned.
        """
        validate_embedding(embedding)
        hypothesis_id = records.generate_id()
        try:
            await self._conn.execute(
                "INSERT INTO hypotheses (id, content, created_at) VALUES (?, ?, ?)",
                (hypothesis_id, content, created_at),
            )
            await self._conn.execute(
                "INSERT INTO vec_hypotheses (embedding, hypothesis_id) VALUES (?, ?)",
                (sqlite_vec.serialize_float32(embedding), hypothesis_id),
            )
            await self._conn.execute(
                "INSERT INTO fts_hypotheses (content, hypothesis_id) VALUES (?, ?)",
                (content, hypothesis_id),
            )
        except sqlite3.IntegrityError as e:
            raise classify_integrity_error(e) from e
        except (sqlite3.Error, ValueError) as e:
            raise StorageError(str(e)) from e
        return HypothesisRecord(id=hypothesis_id, content=content, created_at=created_at)

    async def find_by_id(self, id: str) -> HypothesisRecord | None:
        try:
            cursor = await self._conn.execute(
                "SELECT id, content, created_at FROM hypotheses WHERE id = ?", (id,)
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, ValueError) as e:
            raise StorageError(str(e)) from e
        if row is None:
            return None
        return HypothesisRecord.model_construct(
            id=str(row["id"]), content=row["content"], created_at=row["created_at"]
        )

    async def search(
        self,
        *,
        embedding: Sequence[float],
        query: str,
        weights: tuple[float, float],
        limit: int,
        fan_out: int,
    ) -> list[HypothesisResult]:
        """Two-lane retrieval — sqlite-vec proximity + FTS5 authority.

        The authority lane is skipped when the query is empty (FTS5 MATCH
        errors on an empty string — contrast Postgres, whose
        ``plainto_tsquery`` is simply inert). Query tokens are double-quoted
        to force literal matching, matching Postgres's ``plainto_tsquery``
        which strips all operators.
        """
        validate_search_params(weights=weights, limit=limit, fan_out=fan_out)
        w_prox, w_auth = weights
        per_lane_limit = fan_out * limit

        # Lane 1: proximity — ranked by cosine distance (ascending).
        # ``distance`` is exposed alongside ``rank`` so the projection can
        # compute ``proximity = 1 - distance`` without re-querying the
        # virtual table. LIMIT-style vec0 KNN requires SQLite >= 3.41
        # (older planners never push the limit down to the virtual table,
        # and sqlite-vec then rejects the query); the shipped image and
        # uv-managed dev Pythons both clear that floor.
        l1_cte = (
            "l1_ranked AS ("
            "  SELECT hypothesis_id, distance,"
            "    RANK() OVER (ORDER BY distance ASC) AS rank"
            "  FROM ("
            "    SELECT hypothesis_id, distance FROM vec_hypotheses"
            "    WHERE embedding MATCH ? ORDER BY distance LIMIT ?"
            "  ))"
        )
        params: list[bytes | str | float | int] = [
            sqlite_vec.serialize_float32(embedding),
            per_lane_limit,
        ]

        # Lane 2: authority — ranked by FTS5 BM25 (rank is negative, lower
        # is better, so ORDER BY rank ASC).  Skipped when query is empty.
        safe_query = _sanitize_fts5_query(query) if query.strip() else ""
        if safe_query:
            l2_cte = (
                ", l2_ranked AS ("
                "  SELECT hypothesis_id,"
                "    RANK() OVER (ORDER BY bm25 ASC) AS rank"
                "  FROM ("
                "    SELECT hypothesis_id, rank AS bm25 FROM fts_hypotheses"
                "    WHERE fts_hypotheses MATCH ? ORDER BY rank LIMIT ?"
                "  ))"
            )
            l2_pool = " UNION SELECT hypothesis_id FROM l2_ranked"
            l2_join = " LEFT JOIN l2_ranked l2 ON p.hypothesis_id = l2.hypothesis_id"
            rrf_auth = "COALESCE(1.0 / (60 + l2.rank), 0.0)"
            params.extend([safe_query, per_lane_limit])
        else:
            l2_cte = ""
            l2_pool = ""
            l2_join = ""
            rrf_auth = "0.0"

        # k=60: Cormack et al. 2009 standard RRF constant.
        rrf_prox = "COALESCE(1.0 / (60 + l1.rank), 0.0)"
        # ``proximity`` is the raw cosine similarity in [-1, 1]
        # (1 - cosine_distance from sqlite-vec). Authority-only rows missed
        # the proximity lane and COALESCE to 0.0; negative values would mean
        # genuine vector dissimilarity.
        proximity = "COALESCE(1.0 - l1.distance, 0.0)"

        sql = (
            f"WITH {l1_cte}{l2_cte},"
            " pool AS ("
            f"  SELECT hypothesis_id FROM l1_ranked{l2_pool}"
            " )"
            " SELECT h.id, h.content, h.created_at,"
            f"   (? * {rrf_prox} + ? * {rrf_auth}) AS score,"
            f"   {proximity} AS proximity"
            " FROM pool p"
            " JOIN hypotheses h ON h.id = p.hypothesis_id"
            " LEFT JOIN l1_ranked l1 ON p.hypothesis_id = l1.hypothesis_id"
            f"{l2_join}"
            " ORDER BY score DESC"
            " LIMIT ?"
        )

        all_params: list[bytes | str | float | int] = [
            *params,
            w_prox,
            w_auth,
            limit,
        ]

        try:
            cursor = await self._conn.execute(sql, all_params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, ValueError) as e:
            raise StorageError(str(e)) from e

        return [
            HypothesisResult.model_construct(
                id=str(r["id"]),
                content=r["content"],
                created_at=r["created_at"],
                score=r["score"],
                proximity=r["proximity"],
            )
            for r in rows
        ]
