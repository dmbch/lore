"""PostgreSQL implementation of HypothesisRepository."""

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from lore.repositories import records
from lore.repositories._validation import validate_embedding, validate_search_params
from lore.repositories.postgres._errors import translate
from lore.repositories.records import HypothesisRecord, HypothesisResult


class PostgresHypothesisRepository:
    """Hypothesis storage backed by PostgreSQL + pgvector."""

    # Any: psycopg parameterizes connections by row type; this conn uses per-cursor row factories.
    def __init__(self, *, conn: psycopg.AsyncConnection[Any], fulltext_config: str) -> None:
        self._conn = conn
        # The same regconfig the schema's generated tsvector was built under
        # (enforced by check_health). Travels as a psycopg parameter cast to
        # ::regconfig at query time: never interpolated into SQL.
        self._fulltext_config = fulltext_config

    async def store(
        self, *, content: str, embedding: Sequence[float], created_at: int
    ) -> HypothesisRecord:
        """Create a hypothesis with a generated UUIDv4 and persist it.

        Single INSERT: pgvector stores the embedding as a column on
        the hypotheses table, unlike SQLite's two-table virtual table
        approach. No SAVEPOINT needed.
        """
        validate_embedding(embedding)
        hypothesis_id = records.generate_id()
        try:
            await self._conn.execute(
                "INSERT INTO hypotheses (id, content, created_at, embedding)"
                " VALUES (%s, %s, %s, %s::vector)",
                (hypothesis_id, content, created_at, list(embedding)),
            )
        except psycopg.Error as e:
            translate(e)
        return HypothesisRecord(id=hypothesis_id, content=content, created_at=created_at)

    async def find_by_id(self, id: str) -> HypothesisRecord | None:
        try:
            cur = self._conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT id, content, created_at FROM hypotheses WHERE id = %s", (id,))
            row = await cur.fetchone()
        except psycopg.Error as e:
            translate(e)
        if row is None:
            return None
        # str(): psycopg returns uuid.UUID for UUID columns; records expect str.
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
        """Two-lane retrieval: pgvector proximity + tsvector authority.

        ``plainto_tsquery`` of an empty string matches nothing under any
        regconfig, so the authority lane is naturally inert on an empty
        query, unlike SQLite, whose FTS5 MATCH errors on empty input and
        must be skipped explicitly.
        """
        validate_search_params(weights=weights, limit=limit, fan_out=fan_out)
        w_prox, w_auth = weights
        per_lane_limit = fan_out * limit

        # k=60: Cormack et al. 2009 standard RRF constant. Inlined as a SQL
        # literal so the string stays a LiteralString (psycopg requirement).
        rrf_prox = "COALESCE(1.0 / (60 + l1.rank), 0.0)"
        rrf_auth = "COALESCE(1.0 / (60 + l2.rank), 0.0)"
        # Authority-only rows have no ``l1_ranked`` entry → LEFT JOIN yields
        # NULL distance → COALESCE returns the documented 0.0 "no signal" default.
        proximity = "COALESCE(1.0 - l1.distance, 0.0)"

        sql = (
            "WITH l1_ranked AS ("
            "  SELECT hypothesis_id, distance,"
            "    RANK() OVER (ORDER BY distance ASC) AS rank"
            "  FROM ("
            "    SELECT id AS hypothesis_id,"
            "      embedding <=> %(emb)s::vector AS distance"
            "    FROM hypotheses"
            "    ORDER BY embedding <=> %(emb)s::vector LIMIT %(fan_out)s"
            "  ) sub"
            "),"
            " l2_ranked AS ("
            "  SELECT hypothesis_id,"
            "    RANK() OVER (ORDER BY score DESC) AS rank"
            "  FROM ("
            "    SELECT id AS hypothesis_id,"
            "      ts_rank(fulltext,"
            "        plainto_tsquery(%(fts_cfg)s::regconfig, %(query)s)) AS score"
            "    FROM hypotheses"
            "    WHERE fulltext @@ plainto_tsquery(%(fts_cfg)s::regconfig, %(query)s)"
            "    ORDER BY score DESC LIMIT %(fan_out)s"
            "  ) sub"
            "),"
            " pool AS ("
            "   SELECT hypothesis_id FROM l1_ranked"
            "   UNION"
            "   SELECT hypothesis_id FROM l2_ranked"
            " )"
            " SELECT h.id, h.content, h.created_at,"
            f"   (%(w_prox)s * {rrf_prox}"
            f"    + %(w_auth)s * {rrf_auth}) AS score,"
            f"   {proximity} AS proximity"
            " FROM pool p"
            " JOIN hypotheses h ON h.id = p.hypothesis_id"
            " LEFT JOIN l1_ranked l1 ON p.hypothesis_id = l1.hypothesis_id"
            " LEFT JOIN l2_ranked l2 ON p.hypothesis_id = l2.hypothesis_id"
            " ORDER BY score DESC"
            " LIMIT %(lim)s"
        )

        params = {
            "emb": list(embedding),
            "query": query,
            "fts_cfg": self._fulltext_config,
            "fan_out": per_lane_limit,
            "w_prox": w_prox,
            "w_auth": w_auth,
            "lim": limit,
        }

        try:
            cur = self._conn.cursor(row_factory=dict_row)
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        except psycopg.Error as e:
            translate(e)

        # str(): psycopg returns uuid.UUID for UUID columns; records expect str.
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
