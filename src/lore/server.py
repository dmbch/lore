"""Composition root for ``fastmcp run``: no-arg factory + lifespan-owned system.

``server()`` is the entrypoint the fastmcp CLI loads (``lore.server:server``).
It configures telemetry, loads settings, and returns a FastMCP instance whose
lifespan enters ``system()``: the async context manager owning migrations,
health check, pool lifetime, and orchestrator wiring. Outside the layer model,
like ``__main__`` before it.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastmcp import FastMCP

from lore.adapter import create_server
from lore.config import LoreSettings, load_settings
from lore.domain import StorageError
from lore.math import build_math
from lore.orchestrator import Orchestrator
from lore.providers import build_providers, resolve_dimensions
from lore.repositories import check_health, connect, make_probe, run_migrations
from lore.telemetry import configure_telemetry


@dataclass
class ProbeCell:
    """Mutable holder tying the readiness probe to the pool lifetime.

    Deliberately mutable (the project default is frozen): the factory must
    hand ``create_server`` a stable ``health_probe`` callable before the
    pool exists. The lifespan fills ``probe`` after connect and clears it
    on exit, so ``check()`` raises ``StorageError`` (the /ready 503 shape)
    before startup and after shutdown, and delegates to the live probe in
    between.
    """

    probe: Callable[[], Awaitable[None]] | None = None

    async def check(self) -> None:
        # Capture once so the lifespan clearing ``self.probe`` cannot race the
        # call, even if an await ever lands between the check and the call.
        probe = self.probe
        if probe is None:
            msg = "system not ready: repository pool is not connected"
            raise StorageError(msg)
        await probe()


@asynccontextmanager
async def system(
    settings: LoreSettings, *, cell: ProbeCell | None = None
) -> AsyncGenerator[Orchestrator]:
    """Full system lifecycle as one scope: bootstrap, pool, wiring, teardown.

    Runs migrations + health check, opens the pool, fills ``cell`` (when
    given), and yields a wired ``Orchestrator``. The ``finally`` arm clears
    the cell and closes the pool on any exit, including caller exceptions,
    so ``/ready`` never vouches for a dead pool and connections never leak.
    """
    dim = resolve_dimensions(settings)
    run_migrations(settings=settings, embedding_dim=dim)
    check_health(settings=settings, embedding_dim=dim)
    pool = await connect(settings)
    try:
        if cell is not None:
            cell.probe = make_probe(pool)
        yield Orchestrator(
            pool=pool,
            providers=build_providers(settings),
            math=build_math(settings),
            settings=settings,
        )
    finally:
        if cell is not None:
            cell.probe = None
        await pool.close()


def server() -> FastMCP[Orchestrator]:
    """No-arg factory for ``fastmcp run lore.server:server``.

    Sync: the factory only assembles the server; all I/O is deferred into
    ``system``'s lifespan, entered later by the runtime. ``fastmcp run``
    accepts sync or async factories. Telemetry precedes settings load so
    the ``bootstrap.env`` log from ``load_settings`` routes through the
    structlog bridge. The probe and the orchestrator share one pool via
    ``system``; ``cell.check`` gives ``/ready`` a truthful answer across
    the whole process lifetime.
    """
    configure_telemetry()
    settings = load_settings()
    cell = ProbeCell()
    return create_server(
        settings=settings,
        system=system(settings, cell=cell),
        health_probe=cell.check,
    )
