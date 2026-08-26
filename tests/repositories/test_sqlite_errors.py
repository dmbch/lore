"""Tests for SQLite integrity error classification."""

import sqlite3

from lore.domain import DuplicateRecord, IntegrityViolation, StorageError
from lore.repositories._sqlite._errors import classify_integrity_error


class TestClassifyIntegrityError:
    def test_unique_constraint_returns_duplicate_record(self) -> None:
        err = sqlite3.IntegrityError("UNIQUE constraint failed: hypotheses.id")
        result = classify_integrity_error(err)
        assert isinstance(result, DuplicateRecord)
        assert "UNIQUE constraint failed" in str(result)

    def test_foreign_key_constraint_returns_integrity_violation(self) -> None:
        err = sqlite3.IntegrityError("FOREIGN KEY constraint failed")
        result = classify_integrity_error(err)
        assert isinstance(result, IntegrityViolation)
        assert "FOREIGN KEY constraint failed" in str(result)

    def test_unknown_integrity_error_returns_storage_error(self) -> None:
        err = sqlite3.IntegrityError("NOT NULL constraint failed: hypotheses.content")
        result = classify_integrity_error(err)
        assert type(result) is StorageError
        assert "NOT NULL constraint failed" in str(result)
