"""Repository layer: Protocols, record types, and the pool factory."""

from lore.repositories._cache_store import (
    LoreCacheStore,
    PoolCell,
    sweep_cache_loop,
    sweep_expired_cache,
)
from lore.repositories._factory import check_health, connect, make_probe, run_migrations
from lore.repositories._protocols import (
    AttestationsRepository,
    CacheRepository,
    HypothesisRepository,
    Repositories,
    RepositoryPool,
    RequestRepository,
)
from lore.repositories._records import (
    AttestationRecord,
    CacheEntry,
    DecayWindow,
    HypothesisRecord,
    HypothesisResult,
    LedgerView,
    RequestRecord,
    generate_id,
)
from lore.repositories.config import CacheConfig, PostgresConfig, RetrievalConfig, SqliteConfig

__all__ = [
    "AttestationRecord",
    "AttestationsRepository",
    "CacheConfig",
    "CacheEntry",
    "CacheRepository",
    "DecayWindow",
    "HypothesisRecord",
    "HypothesisRepository",
    "HypothesisResult",
    "LedgerView",
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
    "generate_id",
    "make_probe",
    "run_migrations",
    "sweep_cache_loop",
    "sweep_expired_cache",
]
