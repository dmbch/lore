"""fastmcp-facing storage over ``CacheRepository``.

fastmcp persists operational state through ``AsyncKeyValue`` stores: OAuth
client registrations and upstream tokens (``client_storage``, default: a
local disk store) and MCP session state (``session_state_store``, default:
process memory). Neither default survives an ephemeral container restart
nor spans replicas; backing both with ``CacheRepository`` puts that state
in the ``_cache`` table, wherever the ledger lives. ``LoreCacheStore``
implements the three ``BaseStore`` primitives; ``PoolCell`` defers the pool
reference until the server lifespan connects it (the readiness probe rides
the same cell); ``sweep_expired_cache`` is the expiry sweep the
composition root schedules. The store is deliberately ignorant of what it
holds: the composition root injects a bare instance into the adapter, and
the adapter Fernet-wraps the OAuth lane itself, since the key material
(the OIDC client secret) is adapter-owned and never reaches this layer.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from key_value.aio._utils.managed_entry import ManagedEntry, load_from_json
from key_value.aio.errors import DeserializationError
from key_value.aio.stores.base import BaseStore

from lore.domain import StorageError
from lore.repositories.protocols import RepositoryPool

log = structlog.get_logger(__name__)


@dataclass
class PoolCell:
    """Mutable holder tying cache storage to the pool lifetime.

    Deliberately mutable (the project default is frozen): auth construction
    needs a ``client_storage`` before the pool exists. Each lifespan cycle
    fills ``pool`` after connect and clears it on exit; OAuth flows only run
    once the lifespan has started, so a live server never reads it empty.
    """

    pool: RepositoryPool | None = None


class LoreCacheStore(BaseStore):
    """``BaseStore`` over ``CacheRepository``.

    The value mapping travels as a JSON string in the ``value`` column;
    timestamps travel as epoch-second integers, matching every other
    timestamp column in the schema. ``ManagedEntry`` datetimes are always
    aware UTC: ``is_expired`` compares against an aware now, so a naive
    datetime out of ``_get_managed_entry`` would raise.
    """

    def __init__(self, *, pool_cell: PoolCell) -> None:
        self._pool_cell = pool_cell
        # Lore owns this implementation; the library's unstable-API warning
        # targets its own bundled stores.
        super().__init__(stable_api=True)

    def _connected_pool(self) -> RepositoryPool:
        # Capture once so a lifespan clearing the cell cannot race the call.
        pool = self._pool_cell.pool
        if pool is None:
            msg = "cache storage used before the repository pool connected"
            raise RuntimeError(msg)
        return pool

    async def _get_managed_entry(self, *, collection: str, key: str) -> ManagedEntry | None:
        async with self._connected_pool().session() as repos:
            entry = await repos.cache.get_entry(collection=collection, key=key)
        if entry is None:
            return None
        # The repositories translate validator-failing rows to StorageError;
        # an unparseable value column is the same corruption one layer up,
        # so it must not escape as the library's exception class.
        try:
            value = load_from_json(json_str=entry.value)
        except DeserializationError as e:
            msg = f"corrupt _cache row for ({collection!r}, {key!r})"
            raise StorageError(msg) from e
        return ManagedEntry(
            value=value,
            created_at=datetime.fromtimestamp(entry.created_at, tz=UTC),
            expires_at=(
                None
                if entry.expires_at is None
                else datetime.fromtimestamp(entry.expires_at, tz=UTC)
            ),
        )

    async def _put_managed_entry(
        self, *, collection: str, key: str, managed_entry: ManagedEntry
    ) -> None:
        created_at = managed_entry.created_at
        expires_at = managed_entry.expires_at
        # session(), not transaction(): the upsert is one atomic statement,
        # the pool docstring's stated home for single-statement writes.
        async with self._connected_pool().session() as repos:
            await repos.cache.put_entry(
                collection=collection,
                key=key,
                value=managed_entry.value_as_json,
                # The public ``put`` always stamps created_at; the fallback
                # keeps the NOT NULL column total for direct callers.
                created_at=(
                    int(created_at.timestamp()) if created_at is not None else int(time.time())
                ),
                expires_at=None if expires_at is None else int(expires_at.timestamp()),
            )

    async def _delete_managed_entry(self, *, key: str, collection: str) -> bool:
        async with self._connected_pool().session() as repos:
            return await repos.cache.delete_entry(collection=collection, key=key)


async def sweep_expired_cache(pool: RepositoryPool) -> None:
    """One sweep of expired ``_cache`` rows; failures are logged, not raised.

    A missed sweep costs nothing but disk until the next tick, so no error
    here may take down the caller's lifespan. The broad catch is
    deliberate: for a fire-and-forget task, a propagating exception means
    dying unobserved mid-lifespan and hijacking teardown when the
    ``finally`` awaits it. Cancellation still passes: ``CancelledError``
    is a ``BaseException``.
    """
    try:
        async with pool.transaction() as repos:
            deleted = await repos.cache.delete_expired(now=int(time.time()))
    except Exception:
        log.warning("cache.sweep.failed", exc_info=True)
        return
    if deleted:
        # Routine housekeeping, not operator-actionable: debug, not info.
        log.debug("cache.sweep", deleted=deleted)
