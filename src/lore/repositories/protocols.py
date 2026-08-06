"""Repository Protocols: structural subtyping contracts for storage.

Protocols live alongside the layer they abstract. Implementations just
match the shape: no inheritance required.

See docs/architecture.md: "Single Protocol hides relational + vector."
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import NamedTuple, Protocol

from lore.domain import EvidenceInput, TrustSignal
from lore.repositories.records import (
    AttestationRecord,
    CacheEntry,
    DecayWindow,
    HypothesisRecord,
    HypothesisResult,
    LedgerView,
    RequestRecord,
)


class HypothesisRepository(Protocol):
    """Store and retrieve hypotheses, including vector similarity search."""

    async def store(
        self, *, content: str, embedding: Sequence[float], created_at: int
    ) -> HypothesisRecord:
        """Create a hypothesis with a generated ID and persist it with its embedding.

        Must be called inside ``pool.transaction()``. Some backends
        (SQLite) implement ``store()`` as three sequential INSERTs across
        the relational, vector, and full-text tables; atomicity across
        those statements is provided by the outer transaction, not by an
        inner SAVEPOINT. Under ``pool.session()`` a mid-store failure
        would leave the relational row orphaned.

        On failure inside a transaction, the transaction is poisoned:
        the orchestrator must rollback. Do not attempt further operations.
        """
        ...

    async def find_by_id(self, id: str) -> HypothesisRecord | None:
        """Retrieve a hypothesis by ID, or None if not found.

        The ID must be a valid UUID string (as returned by ``store()``).
        Behavior on invalid IDs is undefined.
        """
        ...

    async def find_recent(self, *, limit: int) -> list[HypothesisRecord]:
        """Return up to ``limit`` hypotheses, newest first.

        Ordered by ``created_at`` descending, ties broken by ``id``
        ascending for determinism. ``limit`` must be >= 1; raises
        ``ValueError`` otherwise.
        """
        ...

    async def search(
        self,
        *,
        embedding: Sequence[float],
        keywords: Sequence[str],
        weights: tuple[float, float],
        limit: int,
        fan_out: int,
    ) -> list[HypothesisResult]:
        """Two-lane Weighted Reciprocal Rank Fusion retrieval.

        Lane 1 (proximity): vector cosine similarity.
        Lane 2 (authority): full-text search over ``keywords``. Each
        keyword is one retrieval term; boundaries are preserved down to
        the SQL so a multi-token keyword can match as a phrase.

        Each lane ranks candidates independently; per-lane scores are
        ``1 / (k + rank)`` (Cormack et al. 2009, k=60). The composite
        score is the weighted sum of per-lane RRF contributions.

        Each lane fetches ``fan_out * limit`` candidates before UNION
        deduplication: wider fan-out raises recall at the cost of more
        rows scanned. ``weights`` must be non-negative and sum to 1.0
        (±0.001 tolerance). ``limit`` and ``fan_out`` must be >= 1.
        Raises ``ValueError`` for invalid weights, limit, or fan_out.
        """
        ...


class AttestationsRepository(Protocol):
    """Append-only, immutable attestation ledger."""

    async def append(self, record: AttestationRecord) -> None:
        """Append an attestation to the immutable ledger.

        ``record.n_oracle_prior`` is the distinct count of prior attesters
        on the hypothesis at write time, excluding the current oracle: a
        snapshot the Recorder computes against the transaction's attestation
        map. Stored on the row so trust scans read the column rather than
        recomputing the count with a correlated subquery.
        """
        ...

    async def find_by_hypothesis(self, hypothesis_id: str) -> list[AttestationRecord]:
        """Return all attestations for a hypothesis, ordered by timestamp."""
        ...

    async def find_by_hypotheses(
        self,
        hypothesis_ids: Sequence[str],
        *,
        window: DecayWindow | None = None,
    ) -> dict[str, LedgerView]:
        """Batch fetch each hypothesis's ledger view.

        With a ``window``, ``rows`` holds only attestations inside the
        5-half-life decay window ``[window.start, window.t_now]``: older
        rows carry under ~3% of their weight, so read paths skip fetching
        them. Without one, ``rows`` is full history.

        ``oracle_count`` (distinct attesting oracles, the synthetic
        transfer carrier among them) and ``last_attested`` are exact over
        full history in both modes, so an all-stale ledger stays
        distinguishable from a never-attested one. Every requested ID is
        present as a key; unattested hypotheses map to an empty view.
        """
        ...

    async def fetch_trust_alignments(
        self,
        *,
        oracle_id: str,
        t_now: int,
        trust_half_life: float,
    ) -> list[TrustSignal]:
        """Fetch raw alignment data for oracle trust computation.

        Returns one row per attestation by ``oracle_id`` within the time
        window (5 * trust_half_life). Each row carries its hypothesis id,
        the oracle's raw confidence, timestamp, c_herd_prior (LAG), and
        the stored n_oracle_prior. The read-time reference is not part of
        this fetch: callers recompute it from ``fetch_herd_evidence``.

        Domain logic (alignment formula, decay weighting, averaging) lives
        in the math service. The orchestrator wires fetch to compute.
        """
        ...

    async def fetch_herd_evidence(
        self,
        hypothesis_ids: Sequence[str],
        *,
        exclude_oracle: str,
        window: DecayWindow,
    ) -> dict[str, list[EvidenceInput]]:
        """Batch fetch others-only fusion evidence per hypothesis.

        Returns, per requested hypothesis ID, the attestations by every
        oracle except ``exclude_oracle``, projected to ``EvidenceInput``
        and ordered by ``(timestamp, id)``. The synthetic ``_transfer``
        oracle is an ordinary includable oracle here. Rows outside
        ``window`` are excluded; a non-finite half-life collapses its
        lower bound to 0.

        Every requested ID is present as a key; hypotheses with no
        qualifying rows map to an empty list, so callers never guard for
        missing keys. An empty others-list is the unwitnessed condition
        in the oracle trust scan.
        """
        ...


class RequestRepository(Protocol):
    """Structured request store. One row per consult call."""

    async def store(self, record: RequestRecord) -> None:
        """Persist a structured request record.

        Call before any attestation write that references ``record.id``:
        the FK requires the request row to exist before any referencing
        attestation row. The orchestrator writes the request row autocommit
        at the top of ``consult()``, satisfying this ordering by
        construction for both read and write paths.
        """
        ...


class CacheRepository(Protocol):
    """Operational key-value cache: OAuth client registrations, upstream
    tokens, and MCP session state, isolated by ``collection``.

    ``put_entry`` is an upsert: a second write to the same ``(collection,
    key)`` overwrites the row. A deliberate departure from the Ledger's
    append-only invariant: OAuth token refresh churns the same key
    repeatedly, and this table holds operational cache state, not
    epistemic evidence.
    """

    async def get_entry(self, *, collection: str, key: str) -> CacheEntry | None:
        """Return the stored entry, or None if absent."""
        ...

    async def put_entry(
        self,
        *,
        collection: str,
        key: str,
        value: str,
        created_at: int,
        expires_at: int | None,
    ) -> None:
        """Insert or overwrite the row for ``(collection, key)``."""
        ...

    async def delete_entry(self, *, collection: str, key: str) -> bool:
        """Delete the row. True if a row existed, False otherwise."""
        ...

    async def delete_expired(self, *, now: int) -> int:
        """Delete rows whose ``expires_at`` has passed. Returns the count.

        Rows with ``expires_at IS NULL`` (OAuth client registrations) are
        never touched: they persist by design. Call inside
        ``transaction()``: the PostgreSQL implementation guards the sweep
        with a transaction-scoped advisory try-lock so concurrent replicas
        do not scan redundantly; a lock held elsewhere makes the sweep a
        no-op returning 0.
        """
        ...


class Repositories(NamedTuple):
    """Bundle of all repository Protocols. Yielded by the pool's scope CMs.

    The orchestrator holds Protocol-typed references: no runtime
    indirection, no wrapper object.
    """

    hypotheses: HypothesisRepository
    attestations: AttestationsRepository
    requests: RequestRepository
    cache: CacheRepository


class RepositoryPool(Protocol):
    """Pool of repository connections. Returned by ``connect()``.

    Two scope-bound entry points: ``session()`` for autocommit fan-outs,
    ``transaction()`` for atomic multi-statement writes. Both yield a
    ``Repositories`` bundle bound to a backend connection that the pool
    acquires on entry and releases on exit.

    Usage::

        pool = await connect("sqlite:///lore.db")
        async with pool.session() as repos:
            record = await repos.requests.store(request_record)
        async with pool.transaction() as repos:
            await repos.hypotheses.store(content, embedding, created_at=ts)
            await repos.attestations.append(...)
        await pool.close()

    Single-statement writes (``requests.store``) and read-side fan-outs
    are at home in ``session()``; multi-statement writes: anything
    where partial application would corrupt invariants, including
    ``hypotheses.store`` on SQLite, must run inside ``transaction()``.
    """

    def session(self) -> AbstractAsyncContextManager[Repositories]:
        """Autocommit scope. Each statement commits independently."""
        ...

    def transaction(self) -> AbstractAsyncContextManager[Repositories]:
        """Atomic scope. Commits on clean exit, rollback on exception."""
        ...

    async def close(self) -> None: ...
