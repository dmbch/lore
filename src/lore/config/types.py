"""Config types: frozen Pydantic models."""

from typing import Self

from pydantic import model_validator

from lore._pydantic import ConfigModel
from lore.adapter.config import AuthConfig, LimitsConfig, OidcConfig, ServerConfig
from lore.math.config import EpistemicsConfig
from lore.prompts.config import PromptsConfig
from lore.providers.config import EmbeddingModelConfig, ModelConfig
from lore.repositories.config import CacheConfig, PostgresConfig, RetrievalConfig, SqliteConfig


class LoreSettings(ConfigModel):
    """The single config object passed to bootstrap."""

    # serverInfo.version. Never empty: FastMCP falls back to fastmcp.__version__
    # on a falsy value, leaking the framework version to clients. The loader fills
    # this from LORE_VERSION (baked into published images); source builds keep the
    # dev marker.
    version: str = "0.0.0+dev"
    dsn: str
    oidc: OidcConfig | None
    base_url: str | None = None

    epistemics: EpistemicsConfig
    embedding: EmbeddingModelConfig
    fast: ModelConfig
    reasoning: ModelConfig
    limits: LimitsConfig
    retrieval: RetrievalConfig
    server: ServerConfig = ServerConfig()
    auth: AuthConfig = AuthConfig()
    postgres: PostgresConfig
    sqlite: SqliteConfig = SqliteConfig()
    cache: CacheConfig = CacheConfig()
    prompts: PromptsConfig

    @model_validator(mode="after")
    def _validate_cross_section(self) -> Self:
        """Enforce the three cross-section invariants the partials can't see alone.

        Each partial validates within its own section; these three span sections
        (auth ↔ oidc, oidc ↔ base_url) and so live on the composer. They fire at
        ``model_validate`` time, which is the genuine load-time boundary;
        ``model_copy(update=...)`` does not re-run them (pydantic v2).
        """
        if self.auth.required and self.oidc is None:
            msg = "[auth] required = true requires OIDC_URL"
            raise ValueError(msg)
        if self.base_url is not None and self.oidc is None:
            msg = "BASE_URL requires OIDC_URL for authenticated HTTP mode"
            raise ValueError(msg)
        if self.oidc is not None and self.base_url is None:
            msg = "OIDC_URL requires BASE_URL for authenticated HTTP mode"
            raise ValueError(msg)
        return self
