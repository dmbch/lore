# TODO

Technical debt and findings discovered during work. Each entry: what, why it matters, options, status.

---

## Providers bundle should have a factory, like repos

**Found:** 2026-05-25, reviewing the composition root.

**What.** `__main__.bootstrap()` hand-assembles the `Providers` NamedTuple inline —
`Providers(embedder=EmbeddingProvider(settings.embedding), interpreter=CompletionProvider(settings.fast), archivist=CompletionProvider(settings.reasoning))`.
The repository layer hides the equivalent wiring behind `repositories.connect(settings)`;
the providers layer has no such factory, so the composition root carries the
role-to-config mapping and names the concrete provider classes.

**Why it matters.** Asymmetry with repos: the construction knowledge (which concrete
class feeds which model role from which settings sub-config) belongs in the providers
module, not the composition root. `__main__` should name a factory and the settings
object, nothing more — same as it does for the pool.

**Fix sketch.** Add `build_providers(settings) -> Providers` to `providers/bootstrap.py`
(already the provider layer's sync bootstrap helper, alongside `resolve_dimensions`),
export from `lore.providers`, and reduce `bootstrap()` to `providers = build_providers(settings)`.
Plain sync function, not the async `connect()` CM shape — provider construction touches
no I/O (LiteLLM + Instructor clients connect lazily). Only production construction site is
`__main__`; tests build `Providers` from doubles and are unaffected.

**Status:** deferred — small, light-path change. Branch + `/review` when picked up.

---

## Layer-owned config partials, composed in `lore.config`

**Found:** 2026-06-07, while planning logging.

**What.** `lore.config` defines every settings sub-model directly — `PostgresConfig`,
`SqliteConfig`, `EmbeddingModelConfig`, `ModelConfig`, `TaskTypeConfig`, the `[server]`,
`[decay]`, `[trust]`, `[retrieval]`, `[limits]`, `[prompts]` blocks — so it carries the
shape knowledge of every layer downstream. Each layer imports from `lore.config`; the
config module imports from nothing structural. The dependency runs the wrong way.

**Why it matters.** Same inversion as the providers-factory entry above, one rung up.
The layer that owns the behavior should own the shape of its configuration. Adding a new
config field today is a `lore.config` edit; in the inverted shape it is a one-file change
in the layer that uses it, with `lore.config` importing the type. Same calling contract
preserved (modules continue to take `LoreSettings` whole) — only the *definition* moves.
The refactor also surfaces the missing `MathConfig`: decay/trust/retrieval/limits are
math-service knobs scattered under sibling sub-tables, and naming a `lore.math.config`
makes the surface area legible.

**Fix sketch.**

```
lore/repositories/config.py    → RepositoryConfig, PostgresConfig, SqliteConfig
lore/providers/config.py       → ProviderConfig, EmbeddingModelConfig, ModelConfig, TaskTypeConfig
lore/adapter/config.py         → AdapterConfig ([server] block, auth_required, OIDC_URL/BASE_URL pair)
lore/telemetry/config.py       → TelemetryConfig (LOG_LEVEL gate, log_format guard)
lore/math/config.py            → MathConfig (decay, trust K, retrieval weights, transfer threshold, limits)

lore/config/__init__.py        → imports each layer's config; assembles
                                  class LoreSettings(BaseModel, frozen=True, strict=True):
                                      adapter: AdapterConfig
                                      repositories: RepositoryConfig
                                      providers: ProviderConfig
                                      math: MathConfig
                                      telemetry: TelemetryConfig
```

No cycles: each layer's `config.py` is a leaf imported by both `lore.<layer>` and
`lore.config`. Static typing preserved end-to-end. The TOML loader still parses against a
single `LoreSettings`. Tests in `tests/config/` move alongside the layer that owns the
type.

**Explicitly rejected alternatives.** Dynamic assembly via `pydantic.create_model()` or
`importlib.metadata` entry-points — both kill strict pyright typing on `LoreSettings`,
since fields would not be statically known. The supermodel here is assembled by hand on
purpose; the "register a partial" cleverness buys nothing the boring composition does not.

**Status:** deferred — incremental, one layer per PR. No behavior change, no test changes
beyond import paths. Ordering: pair with the providers-factory work or land first, since
that entry already inverts one slice of the same dependency.

---

## Deployment docs (platform shape, secrets, IdP, OTel collector)

**Found:** 2026-06-07, while planning the logging refactor.

**What.** Document a dogfooding deployment end-to-end: container/platform shape (persistent volume for SQLite, env mapping for `DATABASE_URL`/`OIDC_URL`/`BASE_URL`/`FASTMCP_TRANSPORT`/`OTEL_*`), the platform's secrets workflow, an OIDC IdP example including workspace-restriction passthrough via `extra_authorize_params`, and an OTLP collector setup (endpoint, tenancy headers, sampling).

**Why it matters.** Walking a new operator through a reference deployment shouldn't require reading the source.

**Status:** deferred — docs-only, no code change.
