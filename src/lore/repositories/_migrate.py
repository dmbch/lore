"""Migration file reader.

Discovers ``NNNN_*.sql`` files from a package directory and returns them
in lexicographic order. Backend-specific bootstrap modules handle
connections, locking, and tracking.
"""

import importlib.resources
import re
from typing import NamedTuple

_MIGRATION_RE = re.compile(r"^\d{4}_.*\.sql$")


class Migration(NamedTuple):
    name: str
    sql: str


def read_migrations(package: str) -> list[Migration]:
    """Read migration files from *package*, sorted by name.

    Only files matching ``NNNN_*.sql`` are included. Uses
    ``importlib.resources``: works with editable installs and built
    distributions. Requires the migration directory to be a Python package
    (``__init__.py`` must exist).
    """
    files = importlib.resources.files(package)
    migrations: list[Migration] = []
    for item in files.iterdir():
        if _MIGRATION_RE.match(item.name):
            migrations.append(Migration(name=item.name, sql=item.read_text(encoding="utf-8")))
    migrations.sort(key=lambda m: m.name)
    return migrations
