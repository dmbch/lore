"""psycopg error → domain exception classifier.

Single point of statement-level translation. ``SerializationFailure``
re-raises unchanged (with ``from None`` to suppress the implicit context
chain) so the pool's transaction context manager converts it to
``RetryableTransactionError`` at the outer boundary; statement-level
wrapping would mask the retry contract.

The SQLite sibling parses the IntegrityError message because sqlite3 has
no exception subtypes; both backends surface the same domain exceptions.
See ``repositories/_sqlite/_errors.py``.
"""

from typing import NoReturn

import psycopg
import psycopg.errors

from lore.domain import DuplicateRecord, IntegrityViolation, StorageError


def translate(e: psycopg.Error) -> NoReturn:
    if isinstance(e, psycopg.errors.SerializationFailure):
        raise e from None
    if isinstance(e, psycopg.errors.UniqueViolation):
        raise DuplicateRecord(str(e)) from e
    if isinstance(e, psycopg.errors.CheckViolation | psycopg.errors.ForeignKeyViolation):
        raise IntegrityViolation(str(e)) from e
    raise StorageError(str(e)) from e
