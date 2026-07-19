"""Encryption posture of OAuth client storage: adapter-owned Fernet.

``_build_auth`` wraps the composition root's bare store in
``FernetEncryptionWrapper`` keyed off its own OIDC settings, so the client
secret never leaves the adapter and the repository layer never sees OIDC
bytes. These tests capture the wrapped store by patching ``OIDCProxy`` and
drive it against a real SQLite-backed ``LoreCacheStore``: ciphertext lands
in the ``_cache`` value column, and an undecryptable row (rotated secret,
different deployment) degrades to a cache miss.
"""

import sqlite3
from collections.abc import AsyncGenerator
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from key_value.aio.wrappers.encryption.fernet import FernetEncryptionWrapper
from pydantic import SecretStr

from lore.adapter import OidcConfig
from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]
from lore.config import LoreSettings
from lore.repositories import LoreCacheStore, PoolCell, RepositoryPool, connect, run_migrations
from tests.repositories._orchestrator_fixtures import make_settings

if TYPE_CHECKING:
    from key_value.aio.protocols.key_value import AsyncKeyValue

# Schema dimension for the throwaway test database. OAuth storage never
# touches vector tables; any valid dimension works.
_SCHEMA_DIM = 8


_TEST_SECRET = SecretStr("test-secret")


def _oidc_settings(
    *, client_secret: SecretStr = _TEST_SECRET, client_id: str = "test-client"
) -> LoreSettings:
    # ``model_copy(update=...)`` skips validation; base_url still travels so
    # the object satisfies the oidc <-> base_url invariant it would carry at
    # load time.
    return make_settings().model_copy(
        update={
            "oidc": OidcConfig(
                discovery_url="https://auth.example.com/.well-known/openid-configuration",
                client_id=client_id,
                client_secret=client_secret,
            ),
            "base_url": "https://lore.example.com",
        }
    )


def _oauth_storage(settings: LoreSettings, *, pool_cell: PoolCell) -> AsyncKeyValue:
    """Capture the client_storage that ``_build_auth`` hands ``OIDCProxy``.

    The isinstance assert is a narrowing device; the wrapper type itself is
    pinned by ``test_build_auth_wraps_the_storage_into_client_storage``
    in ``test_mcp.py``.
    """
    with patch("lore.adapter.mcp.OIDCProxy") as mock_proxy:
        _build_auth(settings, storage=LoreCacheStore(pool_cell=pool_cell))
    storage = mock_proxy.call_args.kwargs["client_storage"]
    assert isinstance(storage, FernetEncryptionWrapper)
    return storage


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "lore.db"


@pytest.fixture
async def sqlite_pool(db_path: Path) -> AsyncGenerator[RepositoryPool]:
    settings = make_settings(dsn=f"sqlite:///{db_path}")
    run_migrations(settings=settings, embedding_dim=_SCHEMA_DIM)
    pool = await connect(settings)
    yield pool
    await pool.close()


class TestOauthStorageEncryption:
    async def test_stored_value_is_not_plaintext_in_db(
        self, sqlite_pool: RepositoryPool, db_path: Path
    ) -> None:
        storage = _oauth_storage(_oidc_settings(), pool_cell=PoolCell(pool=sqlite_pool))
        await storage.put(
            "client-abc", {"token": "plaintext-canary-hunter2"}, collection="mcp-clients"
        )

        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                "SELECT value FROM _cache WHERE collection = ? AND key = ?",
                ("mcp-clients", "client-abc"),
            ).fetchone()

        assert row is not None
        stored = row[0]
        assert isinstance(stored, str)
        assert "plaintext-canary-hunter2" not in stored

    async def test_wrapped_store_round_trips_through_encryption(
        self, sqlite_pool: RepositoryPool
    ) -> None:
        storage = _oauth_storage(_oidc_settings(), pool_cell=PoolCell(pool=sqlite_pool))
        value = {"access_token": "abc", "scopes": ["openid"], "active": True}

        await storage.put("client-abc", value, collection="mcp-clients")

        assert await storage.get("client-abc", collection="mcp-clients") == value

    async def test_wrong_secret_fails_to_decrypt_gracefully(
        self, sqlite_pool: RepositoryPool
    ) -> None:
        """A rotated client secret degrades to a cache miss (re-auth), not a crash."""
        cell = PoolCell(pool=sqlite_pool)
        writer = _oauth_storage(_oidc_settings(client_secret=SecretStr("alpha")), pool_cell=cell)
        reader = _oauth_storage(_oidc_settings(client_secret=SecretStr("beta")), pool_cell=cell)

        await writer.put("client-abc", {"token": "secret"}, collection="mcp-clients")

        assert await reader.get("client-abc", collection="mcp-clients") is None

    async def test_same_secret_different_client_id_derives_different_keys(
        self, sqlite_pool: RepositoryPool
    ) -> None:
        """The salt mixes in the client_id, making it per-deployment: two
        deployments sharing a secret still derive distinct keys, and
        precomputation against the public constant salt buys nothing.
        """
        cell = PoolCell(pool=sqlite_pool)
        writer = _oauth_storage(_oidc_settings(), pool_cell=cell)
        reader = _oauth_storage(_oidc_settings(client_id="other-client"), pool_cell=cell)

        await writer.put("client-abc", {"token": "secret"}, collection="mcp-clients")

        assert await reader.get("client-abc", collection="mcp-clients") is None
