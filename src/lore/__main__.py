"""Composition root — config, telemetry, and system wiring.

``configure()`` is sync (config + telemetry). ``setup()`` is an async CM
scoping the pool lifetime (migrations + health check + connect; closes on
exit). ``bootstrap(settings, pool)`` wires providers/math/orchestrator
over the pool. ``amain()`` enters ``setup`` and runs the server;
``main()`` is the sync wrapper around ``asyncio.run(amain())``.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from lore.adapter import create_server, serve
from lore.config import LoreSettings, load_settings
from lore.math import MathService
from lore.orchestrator import Orchestrator
from lore.providers import CompletionProvider, EmbeddingProvider, Providers, resolve_dimensions
from lore.repositories import (
    RepositoryPool,
    check_health,
    connect,
    make_probe,
    run_migrations,
)
from lore.telemetry import configure_telemetry


def configure(*, toml_path: Path | None = None) -> LoreSettings:
    """Sync bootstrap: configure telemetry first, then load settings and enforce auth opt-in.

    Telemetry runs before any other code can emit logs so the ``bootstrap.env``
    INFO log emitted by ``load_settings`` routes through the structlog stdlib
    bridge instead of being dropped at the stdlib root's default WARNING level.

    The ``auth_required`` check still runs after ``load_settings`` because it consumes
    the loaded settings. ``load_settings`` already enforces ``OIDC_URL ↔ BASE_URL``
    pairing, so the check here only needs to refuse ``oidc is None``.
    """
    configure_telemetry()
    settings = load_settings(toml_path=toml_path)
    if settings.server.auth_required and settings.oidc is None:
        msg = "[server] auth_required = true requires OIDC_URL"
        raise ValueError(msg)
    return settings


@asynccontextmanager
async def setup(settings: LoreSettings) -> AsyncGenerator[RepositoryPool]:
    """Eager bootstrap as a scoped pool lifetime.

    Runs migrations + health check, opens the pool, yields it, and
    closes on exit. ``bootstrap()`` and ``make_probe(pool)`` share the
    yielded pool, so ``/ready`` and the orchestrator see the same
    connections.

    The ``finally`` arm closes the pool on any exception from the yield
    or from caller code inside the scope. Steps added between
    ``connect()`` and ``try:`` would not be guarded — keep new
    post-connect work inside the try block.
    """
    dim = resolve_dimensions(
        model=settings.embedding.model, configured=settings.embedding.dimensions
    )
    run_migrations(settings=settings, embedding_dim=dim)
    check_health(settings=settings, embedding_dim=dim)
    pool = await connect(settings)
    try:
        yield pool
    finally:
        await pool.close()


@asynccontextmanager
async def bootstrap(settings: LoreSettings, pool: RepositoryPool) -> AsyncGenerator[Orchestrator]:
    """Lifespan-scoped wiring: providers, math, orchestrator over an existing pool.

    Pool ownership lives in ``amain``; this context manager only assembles
    the orchestrator and yields it. The pool is not closed here.
    """
    providers = Providers(
        embedder=EmbeddingProvider(settings.embedding),
        interpreter=CompletionProvider(settings.fast),
        archivist=CompletionProvider(settings.reasoning),
    )
    math = MathService(
        c_half_life=settings.decay.attestation,
        t_half_life=settings.decay.trust,
        maturity_k=settings.trust.maturity,
    )
    yield Orchestrator(pool=pool, providers=providers, math=math, settings=settings)


async def amain() -> None:
    """Async entry point. Scopes the pool lifetime around ``serve(create_server(...))``."""
    settings = configure()
    async with setup(settings) as pool:
        probe = make_probe(pool)
        await serve(
            create_server(
                settings=settings,
                system=bootstrap(settings, pool),
                health_probe=probe,
            )
        )


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()  # pragma: no cover
