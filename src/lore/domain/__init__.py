"""Domain vocabulary: shared types and exceptions.

Leaf dependency among the layers. Every layer may import from here; this
package imports nothing but ``lore._pydantic``, layer zero beneath every layer.
"""

from lore.domain.errors import (
    ArchivistResolutionError,
    DomainInvariantError,
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
    FrontierEntry,
    InterpreterInput,
    InterpreterOutput,
    Resolution,
    SearchResult,
    TrustSignal,
    WriteContext,
)

__all__ = [
    "LOCAL_ORACLE",
    "TRANSFER_ORACLE",
    "ArchivistInput",
    "ArchivistOutput",
    "ArchivistResolutionError",
    "AttestationComputed",
    "ConsultLoreRequest",
    "ConsultLoreResponse",
    "DomainInvariantError",
    "DuplicateRecord",
    "EvidenceInput",
    "FrontierEntry",
    "InferenceError",
    "IntegrityViolation",
    "InterpreterInput",
    "InterpreterOutput",
    "Resolution",
    "RetryableTransactionError",
    "SearchResult",
    "StorageError",
    "TrustSignal",
    "WriteContext",
]
