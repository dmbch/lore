"""Repository layer: Protocols, record types, and the pool factory."""

from lore.repositories.cache_store import LoreCacheStore, PoolCell, sweep_expired_cache
from lore.repositories.config import PostgresConfig, RetrievalConfig, SqliteConfig
from lore.repositories.factory import check_health, connect, make_probe, run_migrations
from lore.repositories.protocols import (
    AttestationsRepository,
    CacheRepository,
    HypothesisRepository,
    Repositories,
    RepositoryPool,
    RequestRepository,
)
from lore.repositories.records import (
    AttestationRecord,
    CacheEntry,
    HypothesisRecord,
    HypothesisResult,
    RequestRecord,
)

__all__ = [
    "AttestationRecord",
    "AttestationsRepository",
    "CacheEntry",
    "CacheRepository",
    "HypothesisRecord",
    "HypothesisRepository",
    "HypothesisResult",
    "LoreCacheStore",
    "PoolCell",
    "PostgresConfig",
    "Repositories",
    "RepositoryPool",
    "RequestRecord",
    "RequestRepository",
    "RetrievalConfig",
    "SqliteConfig",
    "check_health",
    "connect",
    "make_probe",
    "run_migrations",
    "sweep_expired_cache",
]
