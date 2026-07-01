"""Repository layer: Protocols, record types, and the pool factory."""

from lore.repositories.config import PostgresConfig, RetrievalConfig, SqliteConfig
from lore.repositories.factory import check_health, connect, make_probe, run_migrations
from lore.repositories.protocols import (
    AttestationsRepository,
    HypothesisRepository,
    Repositories,
    RepositoryPool,
    RequestRepository,
)
from lore.repositories.records import (
    AttestationRecord,
    HypothesisRecord,
    HypothesisResult,
    RequestRecord,
)

__all__ = [
    "AttestationRecord",
    "AttestationsRepository",
    "HypothesisRecord",
    "HypothesisRepository",
    "HypothesisResult",
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
]
