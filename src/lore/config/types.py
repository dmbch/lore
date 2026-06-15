"""Config types — frozen Pydantic models."""

from pydantic import BaseModel, ConfigDict

from lore.adapter.config import AuthConfig, LimitsConfig, OidcConfig, ServerConfig
from lore.math.config import EpistemicsConfig
from lore.prompts.config import PromptsConfig
from lore.providers.config import EmbeddingModelConfig, ModelConfig
from lore.repositories.config import PostgresConfig, RetrievalConfig, SqliteConfig


class LoreSettings(BaseModel):
    """The single config object passed to bootstrap."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

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
    prompts: PromptsConfig
