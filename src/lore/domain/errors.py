"""Domain exceptions — shared error vocabulary."""


class StorageError(Exception):
    """Storage failure — connection lost, disk full, extension failure, corrupt database."""


class DuplicateRecord(StorageError):
    """UNIQUE constraint violation."""


class IntegrityViolation(StorageError):
    """Foreign key constraint violation."""


class RetryableTransactionError(StorageError):
    """SERIALIZABLE conflict — the transaction can be safely retried.

    Surfaced when PostgreSQL aborts a transaction with SQLSTATE 40001
    (``serialization_failure``) at commit. The aborted work was discarded,
    so the caller may re-run the same operation on a fresh snapshot.
    """


class ArchivistResolutionError(Exception):
    """Archivist claimed a hypothesis ID that was not in the retrieved set — a
    trust-boundary failure, not a storage one.
    """


class AuthenticationError(Exception):
    """Identity failure — missing claims, invalid tokens."""


class InferenceError(Exception):
    """Model unavailable, rate limited, timeout, invalid response."""
