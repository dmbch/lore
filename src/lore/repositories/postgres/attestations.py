"""PostgreSQL implementation of AttestationsRepository."""

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from lore.domain import EvidenceInput, StorageError, TrustSignal
from lore.repositories.postgres._errors import translate
from lore.repositories.records import (
    AttestationRecord,
    DecayWindow,
    LedgerView,
    build_attestation_records,
    build_ledger_views,
    group_evidence_rows,
)


class PostgresAttestationsRepository:
    """Append-only attestation ledger backed by PostgreSQL."""

    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self._conn = conn

    async def append(self, record: AttestationRecord) -> None:
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
            translate(e)

    async def find_by_hypothesis(self, hypothesis_id: str) -> list[AttestationRecord]:
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
        self,
        hypothesis_ids: Sequence[str],
        *,
        window: DecayWindow | None = None,
    ) -> dict[str, LedgerView]:
        if not hypothesis_ids:
            return {}
        row_filter = ""
        row_params: tuple[list[str] | int, ...] = (list(hypothesis_ids),)
        if window is not None:
            row_filter = " AND timestamp >= %s AND timestamp <= %s"
            row_params = (*row_params, window.start, window.t_now)
        try:
            cur = self._conn.cursor(row_factory=dict_row)
            await cur.execute(
                f"""SELECT id, hypothesis_id, oracle_id, correlation_id,
                          timestamp, t_oracle, c_oracle_raw,
                          c_oracle_discounted, c_herd, n_oracle_prior
                FROM attestations
                WHERE hypothesis_id = ANY(%s){row_filter}
                ORDER BY timestamp, id""",
                row_params,
            )
            rows = await cur.fetchall()
            # Aggregates are always full-history: "stale since" must stay
            # distinguishable from "never attested" even when the windowed
            # rows above are empty. The count admits the synthetic transfer
            # carrier as one distinct voice, the same policy maturity and
            # the witness rule take (docs/logic.md: formally another
            # oracle); a transfer touches the belief, so MAX(timestamp)
            # spans every row too.
            await cur.execute(
                """SELECT hypothesis_id, COUNT(DISTINCT oracle_id) AS n,
                       MAX(timestamp) AS last
                FROM attestations
                WHERE hypothesis_id = ANY(%s)
                GROUP BY hypothesis_id""",
                (list(hypothesis_ids),),
            )
            stats = {
                str(r["hypothesis_id"]): (int(r["n"]), int(r["last"])) for r in await cur.fetchall()
            }
        except psycopg.Error as e:
            translate(e)
        return build_ledger_views(hypothesis_ids=hypothesis_ids, rows=rows, stats=stats)

    async def fetch_herd_evidence(
        self,
        hypothesis_ids: Sequence[str],
        *,
        exclude_oracle: str,
        window: DecayWindow,
    ) -> dict[str, list[EvidenceInput]]:
        if not hypothesis_ids:
            return {}
        try:
            cur = self._conn.cursor(row_factory=dict_row)
            await cur.execute(
                """SELECT hypothesis_id, c_oracle_discounted, timestamp
                FROM attestations
                WHERE hypothesis_id = ANY(%s)
                AND oracle_id != %s
                AND timestamp >= %s
                AND timestamp <= %s
                ORDER BY timestamp, id""",
                (list(hypothesis_ids), exclude_oracle, window.start, window.t_now),
            )
            rows = await cur.fetchall()
        except psycopg.Error as e:
            translate(e)
        # ``group_evidence_rows`` str()s the UUID-typed column so keys match
        # the input IDs.
        return group_evidence_rows(hypothesis_ids=hypothesis_ids, rows=rows)

    async def fetch_trust_alignments(
        self,
        *,
        oracle_id: str,
        t_now: int,
        trust_half_life: float,
    ) -> list[TrustSignal]:
        start = DecayWindow(t_now=t_now, half_life=trust_half_life).start
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
                -- cases: "no prior row" (first attestation on a fresh
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
                           ) AS c_herd_prior
                    FROM attestations a
                    WHERE a.hypothesis_id IN (SELECT hypothesis_id FROM relevant)
                )
                SELECT e.hypothesis_id, e.c_oracle_raw, e.timestamp, e.c_herd_prior,
                       e.n_oracle_prior
                FROM enriched e
                WHERE e.oracle_id = %s
                AND e.timestamp >= %s
                AND e.timestamp <= %s
                ORDER BY e.timestamp, e.id""",
                (oracle_id, start, t_now, oracle_id, start, t_now),
            )
            rows = await cur.fetchall()
        except psycopg.Error as e:
            translate(e)

        try:
            return [
                TrustSignal(
                    hypothesis_id=str(r["hypothesis_id"]),
                    c_oracle_raw=r["c_oracle_raw"],
                    timestamp=r["timestamp"],
                    c_herd_prior=r["c_herd_prior"],
                    n_oracle_prior=r["n_oracle_prior"],
                )
                for r in rows
            ]
        except ValidationError as e:
            # Source is the database: bounds violation in a stored row
            # surfaces as a storage-layer error, not an inference failure.
            msg = f"malformed attestation row: {e}"
            raise StorageError(msg) from e
