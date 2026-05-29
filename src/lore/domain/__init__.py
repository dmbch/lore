"""Domain vocabulary — shared types and exceptions.

Leaf dependency in the import graph. Every layer may import from here;
this package imports from nothing.
"""

from lore.domain.errors import (
    ArchivistResolutionError,
    AuthenticationError,
    DuplicateRecord,
    InferenceError,
    IntegrityViolation,
    RetryableTransactionError,
    StorageError,
)
from lore.domain.types import (
    LOCAL_ORACLE,
    TRANSFER_ORACLE,
    ArchivistInput,
    ArchivistOutput,
    AttestationComputed,
    ConsultLoreRequest,
    ConsultLoreResponse,
    EvidenceInput,
    InterpreterInput,
    InterpreterOutput,
    Resolution,
    SearchResult,
    TrustSignal,
)

__all__ = [
    "LOCAL_ORACLE",
    "TRANSFER_ORACLE",
    "ArchivistInput",
    "ArchivistOutput",
    "ArchivistResolutionError",
    "AttestationComputed",
    "AuthenticationError",
    "ConsultLoreRequest",
    "ConsultLoreResponse",
    "DuplicateRecord",
    "EvidenceInput",
    "InferenceError",
    "IntegrityViolation",
    "InterpreterInput",
    "InterpreterOutput",
    "Resolution",
    "RetryableTransactionError",
    "SearchResult",
    "StorageError",
    "TrustSignal",
]
