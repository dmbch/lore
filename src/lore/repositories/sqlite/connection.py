"""SQLite connection helper — open and configure with sqlite-vec loaded.

The pool ``SqlitePool`` owns the single ``aiosqlite.Connection`` plus an
``asyncio.Lock`` and exposes ``session()`` / ``transaction()``. This module
exposes only the ``connect`` helper that opens that connection.
"""

import sqlite3

import aiosqlite
import sqlite_vec


async def connect(path: str) -> aiosqlite.Connection:
    """Open a SQLite connection with sqlite-vec loaded.

    Uses autocommit mode (isolation_level=None). Transaction boundaries
    are managed by ``SqlitePool.transaction()`` via explicit BEGIN / COMMIT
    / ROLLBACK — repositories themselves never commit.

    Sets row_factory to sqlite3.Row so queries return named-column access,
    eliminating fragile positional indexing in repository implementations.

    Schema is applied by ``run_migrations()`` in the factory before this
    function is called.
    """
    conn = await aiosqlite.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # ``load_extension`` raises ``sqlite3.OperationalError`` when the SQL
    # ``load_extension`` call fails (e.g. extension API disabled by build
    # flag) and ``OSError`` when the loadable shared library is missing
    # or unreadable. Both indicate a bad install; close the connection
    # we just opened before propagating so the caller does not have to.
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
    except sqlite3.Error, OSError:
        await conn.close()
        raise
    return conn
