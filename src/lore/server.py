"""Composition root: no-arg factory + lifespan-owned system.

``server()`` is the factory the fastmcp CLI loads via ``fastmcp.json`` for dev
and tooling; the image runs the same factory through ``python -m lore``. It
configures telemetry, loads settings, and returns a FastMCP instance whose
lifespan opens a fresh ``system()`` scope per cycle: the async context manager
owning migrations, health check, pool lifetime, and orchestrator wiring.
Outside the layer model.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial

from fastmcp import FastMCP

from lore.adapter import create_server
from lore.config import LoreSettings, load_settings
from lore.domain import StorageError
from lore.math import build_math
from lore.orchestrator import Orchestrator
from lore.providers import build_providers, resolve_dimensions
from lore.repositories import (
    LoreCacheStore,
    PoolCell,
    check_health,
    connect,
    make_probe,
    run_migrations,
    sweep_cache_loop,
)
from lore.telemetry import configure_telemetry

__all__ = ["server", "system"]


async def _check_ready(cell: PoolCell) -> None:
    """/ready probe over the shared pool cell.

    Raises ``StorageError`` (the /ready 503 shape) whenever no lifespan
    cycle is live, and probes the live pool in between.
    """
    # Capture once so the lifespan clearing ``cell.pool`` cannot race the
    # call, even if an await ever lands between the check and the probe.
    pool = cell.pool
    if pool is None:
        msg = "system not ready: repository pool is not connected"
        raise StorageError(msg)
    await make_probe(pool)()


@asynccontextmanager
async def system(
    settings: LoreSettings, *, pool_cell: PoolCell | None = None
) -> AsyncGenerator[Orchestrator]:
    """Full system lifecycle as one scope: bootstrap, pool, wiring, teardown.

    Runs migrations + health check, opens the pool, fills ``pool_cell``
    (when given), starts the expired-cache sweep task, and yields a wired
    ``Orchestrator``. The ``finally`` arm cancels the sweep, clears the
    cell, and closes the pool on any exit, including caller exceptions, so
    ``/ready`` never vouches for a dead pool, cache storage never reaches
    one, and connections never leak.
    """
    dim = resolve_dimensions(settings)
    run_migrations(settings=settings, embedding_dim=dim)
    check_health(settings=settings, embedding_dim=dim)
    pool = await connect(settings)
    sweep = asyncio.create_task(sweep_cache_loop(pool, interval=settings.cache.sweep_interval))
    try:
        if pool_cell is not None:
            pool_cell.pool = pool
        yield Orchestrator(
            pool=pool,
            providers=build_providers(settings),
            math=build_math(settings),
            settings=settings,
        )
    finally:
        sweep.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep
        if pool_cell is not None:
            pool_cell.pool = None
        await pool.close()


def server() -> FastMCP[Orchestrator]:
    """No-arg factory for ``fastmcp run lore.server:server``.

    Sync: the factory only assembles the server; all I/O is deferred into
    ``system``'s lifespan, entered later by the runtime. ``fastmcp run``
    accepts sync or async factories. Telemetry precedes settings load so
    the ``bootstrap.env`` log from ``load_settings`` routes through the
    structlog bridge. One ``PoolCell`` ties every repository-backed
    capability (the readiness probe, the operational state storage) to
    the pool lifetime ``system`` owns; both are composed here and
    injected, so the adapter never imports the repository layer.
    """
    configure_telemetry()
    settings = load_settings()
    pool_cell = PoolCell()
    return create_server(
        settings=settings,
        # A factory, not a CM instance: each lifespan cycle opens a fresh
        # system scope (FastMCP re-enters the lifespan per client session
        # on the in-memory transport).
        system=partial(system, settings, pool_cell=pool_cell),
        health_probe=partial(_check_ready, pool_cell),
        # One bare store; the adapter assigns it to fastmcp's storage
        # slots (session state verbatim, the OAuth lane Fernet-wrapped),
        # so the OIDC secret never transits the composition root or the
        # repository layer. Collections keep the lanes isolated.
        storage=LoreCacheStore(pool_cell=pool_cell),
    )
