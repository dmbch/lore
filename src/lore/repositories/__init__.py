"""Repository layer — Protocols, record types, and the pool factory.

Exports the public API for consumers (orchestrator, bootstrap).
"""

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
    "Repositories",
    "RepositoryPool",
    "RequestRecord",
    "RequestRepository",
    "check_health",
    "connect",
    "make_probe",
    "run_migrations",
]
