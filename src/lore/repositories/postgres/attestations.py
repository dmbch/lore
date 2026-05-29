"""PostgreSQL implementation of AttestationsRepository."""

import math
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from lore.domain import StorageError, TrustSignal
from lore.repositories.postgres._errors import translate
from lore.repositories.records import (
    AttestationRecord,
    build_attestation_records,
)


class PostgresAttestationsRepository:
    """Append-only attestation ledger backed by PostgreSQL."""

    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self._conn = conn

    async def append(self, record: AttestationRecord) -> None:
        """Append an attestation to the immutable ledger."""
        try:
            await self._conn.execute(
                """INSERT INTO attestations
                (id, hypothesis_id, oracle_id, correlation_id, timestamp,
                 t_oracle, c_oracle_raw, c_oracle_discounted, c_herd,
                 n_oracle_prior)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    record.id,
                    record.hypothesis_id,
                    record.oracle_id,
                    record.correlation_id,
                    record.timestamp,
                    record.t_oracle,
                    record.c_oracle_raw,
                    record.c_oracle_discounted,
                    record.c_herd,
                    record.n_oracle_prior,
                ),
            )
        except psycopg.Error as e:
            # ``translate`` maps UniqueViolation → DuplicateRecord (matching
            # the SQLite path's ``classify_integrity_error``),
            # Check/ForeignKey violations → IntegrityViolation, and re-raises
            # SerializationFailure unchanged for the pool's outer translator.
            translate(e)

    async def find_by_hypothesis(self, hypothesis_id: str) -> list[AttestationRecord]:
        """Return all attestations for a hypothesis, ordered by timestamp."""
        try:
            cur = self._conn.cursor(row_factory=dict_row)
            await cur.execute(
                """SELECT id, hypothesis_id, oracle_id, correlation_id,
                          timestamp, t_oracle, c_oracle_raw,
                          c_oracle_discounted, c_herd, n_oracle_prior
                FROM attestations
                WHERE hypothesis_id = %s
                ORDER BY timestamp, id""",
                (hypothesis_id,),
            )
            rows = await cur.fetchall()
        except psycopg.Error as e:
            translate(e)

        return build_attestation_records(rows=rows)

    async def find_by_hypotheses(
        self, hypothesis_ids: Sequence[str]
    ) -> dict[str, list[AttestationRecord]]:
        """Batch fetch attestations for multiple hypotheses."""
        result: dict[str, list[AttestationRecord]] = {hid: [] for hid in hypothesis_ids}
        if not hypothesis_ids:
            return result
        try:
            cur = self._conn.cursor(row_factory=dict_row)
            await cur.execute(
                """SELECT id, hypothesis_id, oracle_id, correlation_id,
                          timestamp, t_oracle, c_oracle_raw,
                          c_oracle_discounted, c_herd, n_oracle_prior
                FROM attestations
                WHERE hypothesis_id = ANY(%s)
                ORDER BY timestamp, id""",
                (list(hypothesis_ids),),
            )
            rows = await cur.fetchall()
        except psycopg.Error as e:
            translate(e)
        for record in build_attestation_records(rows=rows):
            result[record.hypothesis_id].append(record)
        return result

    async def fetch_trust_alignments(
        self,
        *,
        oracle_id: str,
        t_now: int,
        trust_half_life: float,
    ) -> list[TrustSignal]:
        """Fetch raw alignment data for oracle trust computation.

        SQL derives c_herd_prior (LAG) and c_herd_now (FIRST_VALUE DESC)
        via window functions over the immutable ledger. Domain logic lives
        in the math service.
        """
        # math.isfinite guards the SQL boundary: MathService accepts
        # t_half_life=inf as the "no decay" mode, but int(5 * inf) raises
        # OverflowError; int(5 * nan) raises ValueError. Either way the
        # window's lower bound collapses to zero, so every Unix-epoch row
        # is in scope.
        window_start = t_now - int(5 * trust_half_life) if math.isfinite(trust_half_life) else 0
        try:
            cur = self._conn.cursor(row_factory=dict_row)
            await cur.execute(
                """WITH relevant AS (
                    SELECT DISTINCT hypothesis_id
                    FROM attestations
                    WHERE oracle_id = %s
                    AND timestamp >= %s
                    AND timestamp <= %s
                ),
                -- ``c_herd_prior`` is the herd consensus immediately before
                -- this attestation; ``LAG`` orders by ``(timestamp, id)``.
                -- The ``COALESCE`` default ``0.0`` covers two distinct
                -- cases — "no prior row" (first attestation on a fresh
                -- hypothesis) and "stored ``c_herd`` happens to be 0.0".
                -- Both yield ``info = 1`` in the alignment math, so the
                -- conflation is harmless. Anyone using ``c_herd_prior`` as
                -- a "fresh hypothesis" detector must distinguish via
                -- ``n_prior``.
                --
                -- ``n_oracle_prior`` is read straight from the stored
                -- column: the Recorder computed the distinct-prior-oracles
                -- count against the transaction's attestation snapshot
                -- and persisted it at write time. Trust scans read the
                -- column instead of recomputing the count.
                enriched AS (
                    SELECT a.id, a.hypothesis_id, a.oracle_id, a.timestamp,
                           a.c_oracle_raw, a.c_herd, a.n_oracle_prior,
                           COALESCE(
                               LAG(a.c_herd) OVER (
                                   PARTITION BY a.hypothesis_id
                                   ORDER BY a.timestamp, a.id
                               ),
                               0.0
                           ) AS c_herd_prior,
                           FIRST_VALUE(a.c_herd) OVER (
                               PARTITION BY a.hypothesis_id
                               ORDER BY a.timestamp DESC, a.id DESC
                           ) AS c_herd_now
                    FROM attestations a
                    WHERE a.hypothesis_id IN (SELECT hypothesis_id FROM relevant)
                )
                SELECT e.c_oracle_raw, e.timestamp, e.c_herd_prior, e.c_herd_now,
                       e.n_oracle_prior
                FROM enriched e
                WHERE e.oracle_id = %s
                AND e.timestamp >= %s
                AND e.timestamp <= %s
                ORDER BY e.timestamp, e.id""",
                (oracle_id, window_start, t_now, oracle_id, window_start, t_now),
            )
            rows = await cur.fetchall()
        except psycopg.Error as e:
            translate(e)

        try:
            return [
                TrustSignal(
                    c_oracle_raw=r["c_oracle_raw"],
                    timestamp=r["timestamp"],
                    c_herd_prior=r["c_herd_prior"],
                    c_herd_now=r["c_herd_now"],
                    n_oracle_prior=r["n_oracle_prior"],
                )
                for r in rows
            ]
        except ValidationError as e:
            # Source is the database — bounds violation in a stored row
            # surfaces as a storage-layer error, not an inference failure.
            msg = f"malformed attestation row: {e}"
            raise StorageError(msg) from e
