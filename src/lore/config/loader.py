"""Config loader: settings assembly.

Thin orchestrator: load base defaults → overlay vendor → overlay user TOML
→ validate → done. All types live in ``lore.config.types``.
"""

import importlib.resources
import os
import tomllib
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlparse, urlsplit, urlunsplit

import structlog
from pydantic import SecretStr

from lore.adapter.config import OidcConfig
from lore.config.types import LoreSettings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# OIDC URL parsing
# ---------------------------------------------------------------------------


def redact_dsn(dsn: str) -> str:
    """Return ``scheme://host[:port]/path``; drop any user:password netloc credentials.

    Cheap operator-facing redaction for diagnostic logs. Postgres DSNs
    (``postgresql://user:pass@host:5432/db``) come back without
    credentials; SQLite paths (``sqlite:////tmp/lore.db``) round-trip
    unchanged. Query strings and fragments are dropped, as they should
    never carry secrets, but the redaction is defensive.
    """
    parts = urlsplit(dsn)
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def parse_oidc_url(url: str) -> OidcConfig:
    """Parse a DSN-style OIDC URL into an OidcConfig.

    Format: ``oidc://client_id:client_secret@host/path``
    The scheme is rewritten to ``https://`` for the discovery URL.
    """
    parsed = urlparse(url)
    if not parsed.username or not parsed.password or not parsed.hostname:
        msg = "OIDC URL must be in format oidc://client_id:client_secret@host/path"
        raise ValueError(msg)
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    discovery_url = urlunsplit(("https", host, parsed.path, "", ""))
    # keep_blank_values=True so `?prompt=` reaches the IdP and fails loudly there,
    # rather than being silently dropped at parse time with no diagnostic.
    extra = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    return OidcConfig(
        discovery_url=discovery_url,
        client_id=unquote(parsed.username),
        client_secret=SecretStr(unquote(parsed.password)),
        extra_authorize_params=extra,
    )


# ---------------------------------------------------------------------------
# Merge + TOML loading
# ---------------------------------------------------------------------------


def _deep_merge(*, base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge. Overlay wins for scalars/lists; nested dicts recurse."""
    result = dict(base)
    for key, value in overlay.items():
        base_value = result.get(key)
        if isinstance(value, dict) and isinstance(base_value, dict):
            result[key] = _deep_merge(
                base=cast(dict[str, Any], base_value), overlay=cast(dict[str, Any], value)
            )
        else:
            result[key] = value
    return result


def _load_bundled_toml(*, package: str, name: str) -> dict[str, Any]:
    files = importlib.resources.files(package)
    resource = files.joinpath(f"{name}.toml")
    return tomllib.loads(resource.read_text(encoding="utf-8"))


def _load_toml(path: Path) -> dict[str, Any]:
    """Load TOML file, returning empty dict if the file doesn't exist."""
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _detect_vendor() -> str | None:
    """Detect LLM vendor from the env. First vendor whose `api_key` env var is set wins."""
    vendors_pkg = importlib.resources.files("lore.config.vendors")
    entries = sorted(vendors_pkg.iterdir(), key=lambda e: str(e.name))
    for entry in entries:
        name = str(entry.name)
        if not name.endswith(".toml"):
            continue
        data = tomllib.loads(entry.read_text(encoding="utf-8"))
        key: str | None = data.get("api_key")
        if key and os.environ.get(key):
            return name.removesuffix(".toml")
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


TOML_CANDIDATES = (Path("lore.toml"), Path("/etc/lore.toml"))


def discover_toml(
    candidates: tuple[Path, ...] = TOML_CANDIDATES,
) -> Path | None:
    """Find the first existing TOML config from conventional paths."""
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _resolve_env() -> tuple[str, OidcConfig | None, str | None]:
    """Read DSN, OIDC, and BASE_URL from environment. Fail-fast on missing DSN.

    ``DATABASE_URL`` is the canonical name (Heroku/Railway/Fly/Render
    convention). The scheme drives backend dispatch downstream via
    ``is_postgres`` / ``is_sqlite``. The OIDC ↔ BASE_URL pairing invariant
    lives on the ``LoreSettings`` cross-section validator, which fires after
    ``model_validate``; this function only parses.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn or not dsn.strip():
        msg = "DATABASE_URL environment variable is required"
        raise ValueError(msg)
    oidc_url = os.environ.get("OIDC_URL")
    oidc = parse_oidc_url(oidc_url) if oidc_url else None
    base_url = os.environ.get("BASE_URL") or None
    return dsn, oidc, base_url


_BUNDLED_PREFIX = "bundled:"


def _resolve_prompts(prompts: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``bundled:`` prompt references to concrete file paths."""
    resolved: dict[str, Any] = {}
    for key, value in prompts.items():
        if isinstance(value, str) and value.startswith(_BUNDLED_PREFIX):
            relative = value.removeprefix(_BUNDLED_PREFIX)
            parts = relative.split("/")
            package = ".".join(["lore", *parts[:-1]])
            filename = parts[-1]
            resource = importlib.resources.files(package).joinpath(filename)
            resolved[key] = Path(str(resource))
        elif isinstance(value, str):
            resolved[key] = Path(value)
        else:
            resolved[key] = value
    return resolved


def load_settings(
    *,
    toml_path: Path | None = None,
) -> LoreSettings:
    """Load and validate all settings from env + TOML + vendor detection.

    Loading order:
    1. Bundled ``lore.toml``: behavioral defaults (epistemics, limits, retrieval)
    2. Detect vendor → load vendor defaults (stripped of ``api_key``): model defaults
    3. Deep merge vendor over bundled → proto-config
    4. Discover user ``lore.toml`` → deep merge user over proto-config
    5. Resolve ``bundled:`` prompt references to concrete paths
    6. Add DSN, OIDC, and version from env
    7. ``model_validate`` the final config into ``LoreSettings``

    Settings-time INFO log (``bootstrap.env``) emits through the module-level
    structlog logger, which routes to whatever wrapper ``configure_telemetry``
    has installed. ``toml_path`` overrides TOML discovery (useful for tests).
    """
    dsn, oidc, base_url = _resolve_env()

    # 1. Bundled defaults
    config = _load_bundled_toml(package="lore.config", name="lore")

    # 2-3. Vendor defaults as base layer
    vendor = _detect_vendor()
    if vendor is not None:
        vendor_defaults = _load_bundled_toml(package="lore.config.vendors", name=vendor)
        vendor_defaults.pop("api_key", None)
        config = _deep_merge(base=config, overlay=vendor_defaults)

    # 4. User TOML overlay
    if toml_path is None:
        toml_path = discover_toml()
    if toml_path is not None:
        user_toml = _load_toml(toml_path)
        config = _deep_merge(base=config, overlay=user_toml)

    # 5. Resolve bundled prompt references
    if "prompts" in config:
        config["prompts"] = _resolve_prompts(config["prompts"])

    # 6-7. Env + model_validate
    config["dsn"] = dsn
    config["oidc"] = oidc
    config["base_url"] = base_url
    # An empty or unset LORE_VERSION (source builds; the Dockerfile's empty ARG
    # default) leaves the dev marker on LoreSettings.version.
    if version := os.environ.get("LORE_VERSION"):
        config["version"] = version
    settings = LoreSettings.model_validate(config)
    _log_bootstrap_env(settings)
    return settings


def _log_bootstrap_env(settings: LoreSettings) -> None:
    """Emit one structured diagnostic line with the env shape Lore booted with.

    Credentials are redacted (``DATABASE_URL`` user:pass; ``OIDC_URL``
    client_id:client_secret stays buried in ``OidcConfig``, so the log
    surfaces only the credential-free ``discovery_url``). Model strings
    are not env, and Instructor/LiteLLM logs them on provider init.
    """
    log.info(
        "bootstrap.env",
        database_url=redact_dsn(settings.dsn),
        oidc_url=settings.oidc.discovery_url if settings.oidc else None,
        base_url=settings.base_url,
        fastmcp_transport=os.environ.get("FASTMCP_TRANSPORT"),
        fastmcp_host=os.environ.get("FASTMCP_HOST"),
        fastmcp_port=os.environ.get("FASTMCP_PORT"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        otel_service_name=os.environ.get("OTEL_SERVICE_NAME"),
        otel_traces_exporter=os.environ.get("OTEL_TRACES_EXPORTER"),
        otel_metrics_exporter=os.environ.get("OTEL_METRICS_EXPORTER"),
        otel_exporter_otlp_endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
