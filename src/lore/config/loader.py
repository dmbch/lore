"""Config loader — settings assembly.

Thin orchestrator: load base defaults → overlay vendor → overlay user TOML
→ validate → done. All types live in ``lore.config.types``.
"""

import importlib.resources
import os
import tomllib
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse, urlunsplit

import structlog
from pydantic import SecretStr

from lore.config.types import LoreSettings, OidcConfig

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# OIDC URL parsing
# ---------------------------------------------------------------------------


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
    discovery_url = urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))
    return OidcConfig(
        discovery_url=discovery_url,
        client_id=unquote(parsed.username),
        client_secret=SecretStr(unquote(parsed.password)),
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
    """Load a bundled TOML file from a package via importlib.resources."""
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
    ``is_postgres`` / ``is_sqlite``.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn or not dsn.strip():
        msg = "DATABASE_URL environment variable is required"
        raise ValueError(msg)
    oidc_url = os.environ.get("OIDC_URL")
    oidc = parse_oidc_url(oidc_url) if oidc_url else None
    base_url = os.environ.get("BASE_URL") or None
    if base_url and oidc is None:
        msg = "BASE_URL requires OIDC_URL for authenticated HTTP mode"
        raise ValueError(msg)
    if oidc is not None and base_url is None:
        msg = "OIDC_URL requires BASE_URL for authenticated HTTP mode"
        raise ValueError(msg)
    if base_url:
        log.info("transport_mode", mode="http", base_url=base_url)
    else:
        log.info("transport_mode", mode="stdio")
    return dsn, oidc, base_url


_BUNDLED_PREFIX = "bundled:"


def _resolve_prompts(prompts: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``bundled:`` prompt references to concrete file paths."""
    resolved: dict[str, Any] = {}
    for key, value in prompts.items():
        if isinstance(value, str) and value.startswith(_BUNDLED_PREFIX):
            relative = value.removeprefix(_BUNDLED_PREFIX)
            parts = relative.split("/")
            package = ".".join(["lore"] + parts[:-1])
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
    1. Bundled ``lore.toml`` — behavioral defaults (decay, trust, limits, retrieval)
    2. Detect vendor → load vendor defaults (stripped of ``api_key``) — model defaults
    3. Deep merge vendor over bundled → proto-config
    4. Discover user ``lore.toml`` → deep merge user over proto-config
    5. Resolve ``bundled:`` prompt references to concrete paths
    6. Add DSN, OIDC, and version from env
    7. ``model_validate`` the final config into ``LoreSettings``

    Settings-time INFO logs (transport mode) emit through the module-level
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
    return LoreSettings.model_validate(config)
