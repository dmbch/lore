"""SQLite integrity error classification.

sqlite3 has a single IntegrityError for UNIQUE, FK, and CHECK violations.
psycopg has granular subtypes (UniqueViolation, ForeignKeyViolation,
CheckViolation). This module parses the stable SQLite error message to
match psycopg's precision so both backends raise the same repository-layer
exceptions.

SQLite error messages are hardcoded in English in the C source:
  - "UNIQUE constraint failed: <table>.<column>"
  - "FOREIGN KEY constraint failed"
  - "CHECK constraint failed: <expression>"

The Postgres sibling dispatches on psycopg subtypes via ``isinstance``;
the asymmetry is intrinsic to the drivers. Both surface the same domain
exceptions. See ``repositories/postgres/_errors.py``.
"""

import sqlite3

from lore.domain import DuplicateRecord, IntegrityViolation, StorageError


def classify_integrity_error(e: sqlite3.IntegrityError) -> StorageError:
    """Map sqlite3.IntegrityError to the matching repository-layer exception."""
    msg = str(e)
    if "UNIQUE constraint failed" in msg:
        return DuplicateRecord(msg)
    if "CHECK constraint failed" in msg:
        return IntegrityViolation(msg)
    if "FOREIGN KEY constraint failed" in msg:
        return IntegrityViolation(msg)
    return StorageError(msg)
