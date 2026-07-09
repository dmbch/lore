# Architecture

Five layers, hexagonal. Protocol boundaries preserve future choices; every swappable implementation is a decision deferred.

See [logic.md](logic.md) for the formalism. See [IDEA.md](../IDEA.md) for the domain model.

**Why hexagonal.** Lore must swap storage backends (SQLite → PostgreSQL) and LLM vendors (Claude ↔ Gemini) without touching domain logic. Protocol-based structural subtyping keeps layers independently evolvable.

---

## Technology Choices

Python 3.14+ · uv · ruff · pyright · hatchling. All configuration in `pyproject.toml`. Python is the lingua franca of the AI/ML ecosystem: embeddings, LLM clients, vector databases are Python-first. uv and ruff are Rust-fast. Pyright strict mode catches type errors before runtime. Runtime performance is acceptable for an I/O-bound system.

No ORM. Raw SQL for relational operations, vector extensions (pgvector, sqlite-vec) for similarity search. No abstraction layer between the code and the database.

### Concurrency Model

Lore is an async-first system. The MCP adapter, orchestrator, repositories, and providers use `async/await` for all I/O. Thread safety is required for all shared mutable state, even when current callers are single-threaded. The cost of a lock is negligible; the cost of a race condition discovered in production is not.

**Default stance:**
- **Async for I/O.** All repository and provider Protocol methods are `async`. The orchestrator is async. The math service is sync: its operations are pure.
- **Thread-safe for shared state.** Module-level mutable state (singletons, registries, configuration guards) must use `threading.Lock`.
- **Immutable by default.** Pydantic `BaseModel(frozen=True, strict=True)` for validated data at boundaries (records, config, request/response). `NamedTuple` for immutable named structure (math primitives, Protocol bundles, thin facades). No dataclasses. Mutable state is opt-in, documented, and guarded.

---

## Bootstrap

Outside the layer model. The composition root is `lore/server.py`: a no-arg sync factory `server()`. Dev loads it via `fastmcp run` (through `fastmcp.json`); the image runs the same factory through `python -m lore` (a thin `__main__.py`), since a wheel-only image has no source tree for the fastmcp CLI's file spec. Both defer to `run_async`, which reads transport and banner from `FASTMCP_*` env, so the two are runtime-equivalent. The factory only assembles; all I/O is deferred into the lifespan.

The factory body runs three steps:

1. **Telemetry**: `configure_telemetry()` wires structlog and the LiteLLM OTel callback before any other Lore code can emit logs (see Telemetry). SDK provider wiring is delegated to `opentelemetry-instrument`; the glue works against whichever global providers are already installed. The tracer is resolved lazily at first `start_span()` call. When the wrapper is not used, the global providers are OTel API proxies; spans are non-recording but module-level loggers still emit through structlog.
2. **Config**: `load_settings()` builds `LoreSettings` from env vars + `lore.toml` (see Config). The settings-time `bootstrap.env` INFO line emits through structlog because step 1 is already up. Fail-fast on invalid values; cross-section invariants (including the auth↔OIDC check) are validated as the settings model is built, not as a separate procedural step.
3. **Assembly**: `create_server(settings=..., system=system(settings, cell=cell), health_probe=cell.check)` returns the FastMCP instance. `system(settings)` is handed over unentered; the FastMCP lifespan enters it at startup and exits it at shutdown.

`system()` is an async context manager owning the full lifecycle as one scope:

1. **Dimensions**: `resolve_dimensions(settings)` resolves the embedding output size, so the schema always gets a concrete `int`.
2. **Migrations**: `run_migrations(settings, embedding_dim=dim)` applies parameterized SQL schema changes (see Migrations). The `_system` table tracks applied migrations; the run is idempotent, so a `fastmcp run --reload` restart re-applies safely.
3. **Health check**: `check_health(settings, embedding_dim=dim)` stores the embedding model name and dimensions on first run, verifies them on subsequent runs. Mismatch means the vector space is inconsistent; fail-fast with `StorageError`.
4. **Pool**: `connect(settings)` opens the `RepositoryPool`; the scope fills the `ProbeCell` with `make_probe(pool)`.
5. **Providers and orchestrator**: LiteLLM implementations constructed with model strings from config (`build_providers(settings)`), the math service (`build_math(settings)`), and the wired `Orchestrator` yielded through the lifespan to the adapter. `system()` names factories rather than inlining constructor wiring.
6. **Teardown**: the `finally` arm clears the cell and closes the pool on any exit, including caller exceptions, so `/ready` never vouches for a dead pool and connections never leak.

**ProbeCell.** The factory must hand `create_server` a stable `health_probe` callable before the pool exists, so `ProbeCell` is a deliberately mutable holder tying the readiness probe to the pool lifetime. `cell.check` raises `StorageError` (the `/ready` 503 shape) before startup and after shutdown, and delegates to the live probe in between. Probe and orchestrator close over the same pool, filled and cleared inside the same scope.

DSNs and API keys come from environment variables (12-factor); behavioral config from TOML. No layer imports the composition root; the composition root imports all layers.

---

## Layers

### Adapter

MCP is the interface. One tool: `consult`. Two adapters from day one:

- **MCP over stdio**: single-user local development. No auth; oracle identity is the synthetic `_local`.
- **MCP over HTTP**: multi-user. When `OIDC_URL` is configured, the adapter runs OAuth and extracts the oracle identity from the IdP's `sub` claim (see Authentication). Otherwise the adapter falls back to the same `_local` identity, fine for sidecar topologies that authenticate at the proxy; operators who want Lore to refuse the unauthenticated path set `[auth] required = true` in `lore.toml`.

Both translate the MCP protocol into an orchestrator call. No domain logic: parse, validate input shape, delegate. Each receives a fully wired orchestrator from bootstrap. Knows nothing below the orchestrator.

**FastMCP 3 OTel integration.** FastMCP 3 instruments all MCP operations automatically: tool calls, resource reads, errors, auth context. It uses the OTel API; Lore's telemetry module provides the SDK it feeds into. Every `consult` call gets a root span with session tracking and error recording for free.

**Error posture.** `create_server` sets `mask_error_details=True` on the FastMCP instance: hardcoded posture, deliberately overriding the `FASTMCP_*` env default, pinned by a leak test. Any unhandled exception in a tool scrubs to FastMCP's uniform message on the wire; details reach the logs, never the client. `ErrorHandlingMiddleware(transform_errors=False, include_traceback=True)` supplies the log-side record: `transform_errors=False` keeps tool-error semantics (`True` would rewrap tool errors as protocol `McpError`), `include_traceback=True` preserves the `__cause__` chain. Consult keeps two deliberate `ToolError` arms that do reach the client: the client-fault arm surfaces the violated domain rule verbatim (the constant the domain validator wrote for the Scribe, never pydantic's repr, which would echo the client's payload), and the auth arm rejects an access token whose `sub` claim is not a string. Correlation ids are ledger and trace identity (the OTel `trace_id`, uuid4 fallback when no span records) and are never client-facing.

**Healthcheck routes.** The HTTP transport exposes two operator-facing endpoints alongside the MCP path:

- `GET /health`: liveness probe. Always returns `200 {"status": "ok"}`. Confirms the process is responsive without touching the database; load balancers use it to detect a deadlocked or unresponsive container.
- `GET /ready`: readiness probe. Awaits an injected `health_probe` callable; returns `200 {"status": "ok"}` on success and `503 {"status": "unavailable"}` when the probe raises. `StorageError` is the expected failure (logged at WARNING under `ready.unavailable`); any other exception is logged at ERROR under `ready.error.internal` with `exc_info=True` and collapses to the same scrubbed 503: the wire posture stays uniform regardless of the underlying cause. When `health_probe` is omitted (stdio mode, tests), `/ready` returns 200 unconditionally.

The probe is composed by `repositories.make_probe(pool)` and reaches `create_server` as the injected `health_probe`. The adapter never imports the repository layer; the composition root (`lore/server.py`) owns the pool inside its lifespan scope, so the probe closes over the *same* pool the orchestrator uses. A passing `/ready` therefore answers the actual readiness question: "can a consult call get a working connection right now?"

**Live-pool semantics.** The probe acquires from the pool via `session()` and releases immediately, bounded by `asyncio.timeout` so a hung borrow translates to a scrubbed `StorageError`. Each backend validates connections at the scope boundary: Postgres via `check=AsyncConnectionPool.check_connection` on every `getconn()`, SQLite via a `SELECT 1` on every `session()` entry. Half-closed connections are discarded before any caller sees them. Cost: one `SELECT 1` per scope. A consult opens two `session()` scopes (request store, retrieval) plus an optional `transaction()` on the write path: 2–3 validations per consult, all in-process for SQLite and one network roundtrip each for Postgres.

Under sustained load the live-pool probe will report 503 when the pool saturates, which is the correct K8s shedding behavior: load balancers drain traffic from the saturated pod, HPA scales out on the unready signal, and clients see 503 rather than 5xx-with-retries. The alternative (a transient-connection probe that sidesteps the pool) would happily report 200 while real requests hang on pool acquisition: false-positive readiness, not a feature.

### Orchestrator

Wires use cases. Receives repositories and providers as Protocol-typed arguments, never their implementations. Calls service functions, coordinates I/O through injected dependencies, manages transaction boundaries. One function per use case. No shared mutable state.

**Write path trust grading.** On each write, the orchestrator coordinates within a single transaction: compute oracle trust (repository query) → fetch attestations for the union of corroborated and contradicted hypothesis IDs (one repository query) → dispatch each Resolution to the Recorder. The Recorder applies the trust discount, fuses with ECBF, and persists attestations. A `corroborates` resolution yields a positive attestation on the corroborated hypothesis; a `contributes` resolution stores the novel and writes a positive attestation on it. Either form may carry `contradicts: list[HypothesisId]`, producing a negative attestation per contradicted hypothesis. A `contributes` resolution with non-empty `contradicts` additionally writes one consolidated transfer attestation on the novel: the negated decayed-ECBF fusion of the latest `c_herd` per contradicted hypothesis, recorded under the synthetic `_transfer` oracle with full credibility (no second source discount). Reads inside the transaction are snapshot-consistent with subsequent writes.

**Write-path isolation and retry.** The Postgres write-path transaction runs at SERIALIZABLE. Two concurrent consults on the same hypothesis would otherwise write-skew: both reading the same prior `c_herd`, both writing attestations against it, leaving the second row's `c_herd` stale. SERIALIZABLE detects the dependency cycle and aborts one committer with SQLSTATE 40001. `PostgresPool.transaction()` translates `psycopg.errors.SerializationFailure` to `RetryableTransactionError` (a `StorageError` subclass); the orchestrator catches it and retries `record()` up to three times with 10 ms / 20 ms exponential backoff. SQLite needs none of this (its single-connection lock already serializes writes) and the SQLite pool keeps autocommit-per-statement semantics for `session()` and a sync transaction for `transaction()`.

**Pre-record validation.** Between Reason and Record, the orchestrator's validator (`validate.validate_resolutions`) runs a set-membership check against the retrieved set the Archivist saw. Every `corroborates` and `contradicts` ID claimed by the Archivist must appear in the enriched candidate list `reason()` was given; anything else is a hallucinated UUID and is rejected with `ArchivistResolutionError`. No extra DB calls, no re-embedding; the IDs are already in memory. Hallucinated IDs are the one failure mode the epistemics cannot digest: misclassified relationships (a paraphrase labeled novel, an orthogonal claim labeled contradiction) are absorbed by trust discounting, ECBF, and decay, but a UUID with no grounding is a foreign body the math has no way to handle. On the wire the error scrubs to FastMCP's uniform masked message (see Error posture under Adapter); the original message survives in the logs.

### Services

Coherent units of behavior the orchestrator delegates to. The math service (fusion, decay, confidence mapping, trust discounting, maturity) is the canonical case: pure functions, sync, no I/O or Protocols, data in, data out. Other services may coordinate I/O when the role calls for it; purity is a property of the operation, not a layer-wide rule.

`Opinion` (the BDU triple) is internal to `lore.math`. The math/orchestrator boundary uses scalar confidences `c ∈ [-1, 1]` and domain types from `lore.domain`. The math service accepts the full mathematical domain; trust discounting (P_effective < 1 for K ≥ 1) ensures pipeline values remain in (-1, 1) exclusive. It accepts domain types, does the algebra internally with `Opinion`, and returns domain types with scalars.

### Repositories

Storage abstractions. Define and own their Protocols, implement them. SQLite + sqlite-vec for development. PostgreSQL + pgvector for production. Both proven, both boring. Self-contained, with no dependencies on other layers.

Swapping SQLite for PostgreSQL requires a new Protocol implementation; no service code changes. The data model has three (logical) tables: hypotheses (with embedding + tsvector for FTS), attestations (ledger, with `id` PK and nine fields; see IDEA.md), and provenance (every request as structured columns mirroring the consult payload). No oracle table; oracle identity is a plain string on the ledger. All hypotheses (composite and atomic) share the same table and formalism. No type column, no structural link between composite and atomic propositions.

**Orphan request rows are evidence, not garbage.** The orchestrator stores the request row autocommit before any downstream stage opens. If the Interpreter 5xxs, embedding times out, the validator rejects an Archivist resolution (anything fails after the request row is written), the row stays in place with zero joining attestations. "Orphan" rows are the documented provenance contract ("storage is cheap, information is valuable"): meaningful evidence of attempted consults, not bugs to clean up. Operators surface them with:

```sql
SELECT r.* FROM requests r
LEFT JOIN attestations a ON a.correlation_id = r.id
WHERE a.id IS NULL
```

**Attestation indexes.** Two composite indexes on `attestations` cover all access patterns: `(oracle_id, timestamp)` for the trust scan inner subquery ("find hypotheses this oracle touched recently"), and `(hypothesis_id, timestamp)` for window functions, ECBF fetch, maturity count, and `c_herd_prior` derivation.

**Two-lane retrieval.** The repository layer owns a single SQL query per backend that implements hybrid search: Lane 1 (proximity: vector cosine) and Lane 2 (authority: FTS). Both lanes fan out at 2× the configured limit, UNION deduplicates into a candidate pool. The composite score uses weighted Reciprocal Rank Fusion (Cormack et al. 2009) with configurable lane weights. The `proximity` field reports the cosine similarity from Lane 1 when available and falls back to `0.0` otherwise; authority-only rows (those that surface via Lane 2 but not Lane 1) carry `proximity = 0.0` on both backends as the "no signal" default.

**FTS language configuration.** Both backends apply English stemming and stopword filtering by default: `[postgres] fulltext_config = "english"` and `[sqlite] fulltext_config = "porter unicode61"`. Postgres operators can switch to any installed text-search configuration (`german`, `french`, `simple`, ...). SQLite's built-in `porter` stemmer is English-only; non-English SQLite deployments should set `fulltext_config = "unicode61"` (no stemming) unless they ship a third-party tokenizer. The value is bound at schema-creation time. Changing it on an existing database requires rebuilding the FTS index, and the bootstrap health check refuses to start on mismatch.

**Records mirror the schema and double as the orchestrator-visible shape.** Repositories own Pydantic models that mirror the database schema. `HypothesisRecord` is `id`, `content`, `created_at`. `AttestationRecord` is the ten-field ledger row including trust assessment (`t_oracle`), raw and discounted confidence (`c_oracle_raw`, `c_oracle_discounted`), herd snapshot (`c_herd`), and the write-time prior-attester count (`n_oracle_prior`). Each record validates on construction so corrupt data fails loudly; hot-path reads use `model_construct()` to skip validation since the database already enforces the same constraints. There is no separate "domain entity" layer: the orchestrator consumes records directly, and the read-time epistemic projection lives on `SearchResult` (in `lore.domain`), which carries the hypothesis fields it surfaces alongside `c_herd`, `attestation_count`, `last_attested`, and the retrieval scores.

**Factory function, not facade.** `connect(settings)` opens a connection, initializes extensions (sqlite-vec or pgvector), and returns a Protocol-typed `RepositoryPool`. `run_migrations(settings, embedding_dim=...)` and `check_health(settings, embedding_dim=...)` are separate sync bootstrap steps that run before `connect()`. `make_probe(pool, *, timeout=5.0)` returns a no-arg coroutine that acquires from the pool and releases: the live-pool readiness probe `/ready` awaits (see Adapter). The three settings-taking entry points read the backend choice from `settings.dsn`, so the composition root never sees backend-specific knobs. No runtime indirection: the orchestrator holds Protocols, not a wrapper object. The factory is bootstrap infrastructure, not a layer. Usage:

```python
run_migrations(settings, embedding_dim=dim)
check_health(settings, embedding_dim=dim)
pool = await connect(settings)
async with pool.session() as repos:
    await repos.requests.store(record)
async with pool.transaction() as repos:
    await repos.attestations.append(...)
await pool.close()
```

**Batteries included.** Lore ships with both backends as runtime dependencies. The factory imports both unconditionally: no lazy loading, no optional extras. A deployment chooses its backend via DSN at runtime, not by installing different packages. This keeps both backends always-tested under the drift guard, and eliminates "works on my machine" divergence. The import cost is negligible for an I/O-bound system.

**SQLite topology.** Single-instance with a local file or persistent volume. SQLite's per-connection file lock and WAL journal mode handle local concurrency. NFS/SMB-mounted database files break `fcntl` semantics and are unsupported. The bootstrap migration path assumes a single writer; concurrent multi-process migration runs are not supported.

**Scope-bound context managers.** `RepositoryPool` exposes two scope kinds, each yielding a `Repositories` bundle bound to a backend connection. `pool.session()` is autocommit: each statement commits independently, the right shape for single statements and read-side fan-outs. `pool.transaction()` wraps the body in a real DB transaction, the right shape for atomic multi-statement writes. Both acquire on entry and release on exit, so a connection is held only for the work it guards. The orchestrator never names the underlying connection, and `consult` opens its scopes per stage so no DB resource is held across an LLM round-trip.

**Migrations.** Lightweight custom runner: raw SQL files read via `importlib.resources`, applied in lexicographic order, tracked in the `_system` table. PostgreSQL uses an advisory lock for concurrency safety. No external dependency. Boring technology.

**Parameterized migration SQL.** Migration templates use named placeholders (e.g. `{embedding_dim}`, `{fulltext_config}`) that are substituted at apply time. `read_migrations()` returns raw SQL; the backend bootstrap modules format and apply. SQL injection is closed off at the settings layer: `PostgresConfig.fulltext_config` and `SqliteConfig.fulltext_config` are validated against a strict identifier regex, and `embedding_dim` is a strict `int`. By the time `run_migrations()` formats the template, the values are trusted by construction. Current placeholders: `{embedding_dim}` for vector column dimensions (`float[{embedding_dim}]` in sqlite-vec, `VECTOR({embedding_dim})` in pgvector) and `{fulltext_config}` for the Postgres `regconfig` or SQLite FTS5 tokenize spec.

**Separate migration sets per backend.** SQLite and PostgreSQL have fundamentally different vector and FTS models: `CREATE VIRTUAL TABLE` with sqlite-vec/FTS5 vs `VECTOR(n)` column + GIN tsvector with pgvector. Shared migrations would fight dialect differences constantly. ~50 lines of duplication for three tables is cheaper than a dialect abstraction layer. KISS over DRY.

**Single Protocol for relational and vector operations.** Each repository Protocol exposes both relational methods (`store`, `find_by_id`) and vector methods (`search`) through a single interface. Whether embeddings live in a virtual table or a column is an implementation detail hidden behind the Protocol. Extension initialization (sqlite-vec, pgvector) belongs in the factory, not the repository.

**Drift guard.** A deterministic test asserts structural equivalence between the SQLite and PostgreSQL schemas: same tables, same columns, same logical types, same indexes, same foreign key constraints. This is the hard guarantee that the backends don't diverge. A Claude rule provides dev-time awareness: check both migration sets when editing either one.

**Embedding precision: float32 everywhere.** SQLite uses `sqlite_vec.serialize_float32` (32-bit floats). pgvector's `VECTOR` type stores float32. No `HALFVEC`, no float16; precision loss in the vector space means silent retrieval degradation with no error signal. This is a hard constraint, not a performance trade-off.

### LLM Providers

Vendor-neutral inference facades. Define and own their Protocols, implement them. Self-contained, with no dependencies on other layers. The Interpreter and Archivist don't know which model or vendor backs them.

**Three model roles.** Each maps to an IDEA.md actor:

| Role | Actor | Purpose | Profile |
|---|---|---|---|
| `embedding_model` | Vector space | Embed hypotheses and queries | Fast, cheap, high-throughput |
| `fast_model` | Interpreter | Normalize jargon, extract retrieval keywords, decompose composites | Fast and cheap; mechanical text transformation |
| `reasoning_model` | Archivist | Semantic resolution, classify relationships | Slow and expensive; genuine reasoning |

Model strings come from config (`LoreSettings`), injected at construction.

**LiteLLM behind Protocols.** LiteLLM provides vendor portability day one through a single `completion()` / `embedding()` API. It handles vendor authentication, retry, and model routing. The Protocol boundary hides LiteLLM entirely; swapping to direct SDKs later requires only a new Protocol implementation, with no service or orchestrator changes. LiteLLM exceptions are caught in the provider implementation and raised as domain `InferenceError`. No LiteLLM types cross the Protocol boundary.

**Task type passthrough.** The embedding provider accepts an optional `task_type` string and passes it through to LiteLLM as a kwarg. The value comes from `EmbeddingModelConfig.task_type`, a `TaskTypeConfig` that maps semantic keys (`document`, `question`, `verification`) to vendor-specific strings. The orchestrator selects the appropriate key for each execution loop stage. Vendors that don't support task types ignore the kwarg; LiteLLM handles the filtering.

**Vendor auto-detection.** When no models are specified in TOML, bootstrap detects which API key is present and applies vendor defaults. Priority is lexical by vendor filename; adding a vendor is just adding a TOML file. TOML overrides are per-role, so you can mix vendors. Startup always logs which models are in use.

**Pass-through model-role configs.** `EmbeddingModelConfig` and `ModelConfig` are pass-through containers by design. Lore types only the fields it consumes itself (`model`, `dimensions`, `task_type` for embedding; `model`, `temperature`, `max_tokens`, `reasoning_effort` for completion roles). Every other TOML key under `[embedding]`, `[fast]`, or `[reasoning]` round-trips via `model_dump()` and flows to LiteLLM unchanged; that is the design commitment, not an accident. The provider implementations forward extras through a small typed wrapper (`_call_litellm_embedding`, the `instructor` client call in `CompletionProvider`) that admits `**extra: Any`. The `Any` is intentional: LiteLLM's kwarg surface is open, and Lore commits to forwarding it without interpretation. Concentrating the vendor-type boundary in those wrappers keeps the rest of the codebase free of `# type: ignore`.

---

## Protocol Design

- `typing.Protocol` for all abstractions between layers (structural subtyping).
- No ABCs. Protocols don't require inheritance; implementations just match the shape.
- Protocols live alongside the layer they abstract: storage Protocols in repositories, inference Protocols in providers.

---

## Cross-Cutting Concerns

### Domain Module

Shared vocabulary for the entire system. The `lore.domain` package defines domain types (frozen Pydantic `BaseModel`s like `TrustSignal`) and domain exceptions (`StorageError`, `InferenceError`, `DuplicateRecord`, `IntegrityViolation`). No logic, no I/O, just pure data definitions. Every layer imports from `lore.domain`; `lore.domain` imports from nothing. It is the leaf dependency in the import graph.

Repositories and providers catch implementation-specific errors and raise domain exceptions. The adapter catches domain exceptions and maps them to MCP error responses. No layer ever imports implementation-specific errors from another layer.

### Config

Frozen `LoreSettings` Pydantic model as the public API, built from `os.environ` + `tomllib`. Two disjoint sources, each field with exactly one authoritative source:

- **Environment variables** (no prefix): secrets and deployment topology. `DATABASE_URL` (scheme-driven: `postgresql://` / `postgres://` → Postgres, `sqlite:///` → SQLite); `OIDC_URL`, `BASE_URL` (paired; both required to enable OAuth on HTTP transport). FastMCP-managed (read by FastMCP itself, not by `LoreSettings`): the `FASTMCP_*` surface, deployment topology above all: `FASTMCP_TRANSPORT`, `FASTMCP_HOST` (default 127.0.0.1), `FASTMCP_PORT` (default 8000), `FASTMCP_SHOW_SERVER_BANNER`, `FASTMCP_CHECK_FOR_UPDATES`, `FASTMCP_STATELESS_HTTP`; see [FastMCP settings](https://gofastmcp.com/more/settings).
- **TOML file** (`lore.toml`): behavioral config. Server identity (`[server]`: `name`, `icon_url`), auth knobs (`[auth]`: `required`, `verify_id_token`), epistemic hyperparameters (`[epistemics]`: `attestation_half_life`, `trust_half_life`, `maturity_k`, `transfer_threshold`), model strings per role (`[embedding]`, `[fast]`, `[reasoning]`), retrieval config (`[retrieval]`: weights, limits, `max_keywords`), character limits (`[limits]`), prompt templates (`[prompts]`).

**No FastMCP wrapping.** `fastmcp.settings` is already a validated, env-driven model; mirroring it into `lore.toml` would give the same knob two authoritative sources, which this design forbids. The boundary: Lore's TOML owns Lore's identity and behavior (server name, icon, Scribe instructions, the OIDC DSN with its cross-section invariant); `FASTMCP_*` env owns deployment topology (transport, host, port, banner, update check, statelessness). One exception is hardcoded rather than env-driven: `mask_error_details=True` is posture, pinned by the leak test (see Adapter). Lore sets no `FASTMCP_*` defaults in code: the fastmcp settings singleton snapshots the environment at `import fastmcp`, before any Lore code loads (under `fastmcp run` or `python -m lore`). Defaults live where timing is guaranteed: the Dockerfile ENV block (image) and mise `[env]` (repo), both operator-overridable.

**Layer-owned config partials.** Each layer defines the config models it consumes: `lore/providers/config.py`, `lore/repositories/config.py`, `lore/adapter/config.py`, `lore/prompts/config.py`, and `lore/math/config.py` (for `EpistemicsConfig`). `lore.config` imports and composes these partials into the flat `LoreSettings`; for config shape the dependency runs layer→nothing, and `lore.config` is the composition root.

**Two validation tiers.** Partials validate within their own section (field constraints); `LoreSettings` validates across sections via a `model_validator`: the `auth.required → oidc` and `oidc ↔ base_url` pairings.

**TYPE_CHECKING import rule.** Layers that take `LoreSettings` import it under `if TYPE_CHECKING:` only; runtime `LoreSettings` use is confined to `lore.config`, `lore.server`, and tests. This keeps `lore.config → layer partials` acyclic.

**1:1 TOML↔field invariant.** TOML sections map one-to-one onto `LoreSettings` fields; `LoreSettings` stays flat, with no nesting.

**Sizing K for your herd.** K governs how quickly `M = N_O / (N_O + K)` saturates, so the right value depends on how many distinct oracles a typical hypothesis attracts. K = 1 is the recommended default. Small herds (fewer than ~10 active oracles) may prefer K = 0.5 for a faster maturity ramp, so that fewer distinct attestors are needed before the discount lifts. Large herds (100+ active oracles) may prefer K = 2 to impose a stricter diversity requirement before a hypothesis is treated as mature. Guidance, not prescription: deployers who change K should understand that the same value also governs the adaptive blend in oracle trust (see `docs/logic.md`).

**Embedding config.** The `[embedding]` section supports three fields beyond the model string:

```toml
[embedding]
model = "gemini/gemini-embedding-001"
dimensions = 1536                        # optional; resolved from LiteLLM model info if omitted

[embedding.task_type]                    # optional; vendor-specific task type strings
document = "RETRIEVAL_DOCUMENT"
question = "QUESTION_ANSWERING"
verification = "FACT_VERIFICATION"
```

`dimensions` (`int | None`, default `None`). When omitted, bootstrap resolves the native output size from `litellm.get_model_info(model)["output_vector_size"]`, a local cost-map lookup rather than a network call. Deployers can override in TOML for Matryoshka truncation. Resolution happens before migrations, so the schema always gets a concrete `int`.

`task_type` (`TaskTypeConfig | None`). A sub-table mapping semantic keys (`document`, `question`, `verification`) to vendor-specific strings passed through to LiteLLM as kwargs. Sparse configs are fine; set only the task types you need, and the provider omits any that are `None`. Gemini supports granular task types; OpenAI and Bedrock Titan v2 have none. `extra="forbid"` catches typos.

**Vendor defaults.** Vendor default files (`config/vendors/{vendor}.toml`) specify model strings and an `api_key` env var name for auto-detection, with no dimensions. Dimensions are always resolved at bootstrap. Gemini vendor defaults include task_type for the three execution loop stages. All vendors specify `reasoning_effort` for the reasoning model.

| Vendor | Embedding | Fast | Reasoning |
|--------|-----------|------|-----------|
| Gemini | `gemini-embedding-001` | `gemini-flash-lite-latest` | `gemini-flash-latest` |
| OpenAI | `text-embedding-3-small` | `gpt-4.1-mini` | `o4-mini` |
| Bedrock | `titan-embed-text-v2:0` | `nova-2-lite-v1:0` | `nova-2-pro-preview-20251202-v1:0` |

No overlap: env vars do not override TOML fields. Single validated object: a bad DSN or missing models fail fast at startup. Vendor auto-detection fills model defaults from API keys (first lexical match wins: Bedrock > Gemini > OpenAI) when TOML is silent; TOML overrides vendor defaults per-role. TOML is discovered from conventional paths: `./lore.toml` (project-local) then `/etc/lore.toml` (system-wide). First found wins. Neither found → defaults only.

`OIDC_URL` encodes the IdP's full OIDC discovery-document URL plus client credentials in one DSN-style string (`oidc://client_id:secret@host[:port]/.well-known/openid-configuration`). The path is used verbatim as the OIDCProxy `config_url`: Lore appends no discovery suffix. Bootstrap parses it, strips credentials before any telemetry is active. Credentials are never logged.

**API key handling asymmetry.** Vendor API keys (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`) are never read by Lore: LiteLLM reads them from the environment at call time, so they never enter Lore's config or code. They are therefore not `SecretStr`-wrapped. `SecretStr` is reserved for `OIDC_URL`'s `client_secret`, which Lore does hold briefly before bootstrap strips it. The asymmetry is deliberate.

### Telemetry

Configuration is centralized in `lore.telemetry`. Acquisition is decentralized: modules that need structured logging use `structlog.get_logger(__name__)` at module scope; modules that need to open a span import `start_span` from `lore.telemetry`. The math service stays pure, with no telemetry imports.

**Logging.** structlog as the logging API, routed through stdlib's `LoggerFactory` so the root logger's level gate filters all messages uniformly across both structlog-originated and stdlib-bridged (FastMCP, LiteLLM) emitters. Consumers acquire loggers via `structlog.get_logger(__name__)` at module scope rather than receiving a threaded handle. One sink: stderr renderer, always on so the system never goes blind. `LOG_LEVEL` (no prefix, read by telemetry, not part of `LoreSettings`) controls stderr verbosity. stdout is the MCP transport; logs never go there. Renderer is auto-detected: `ConsoleRenderer` when stderr is a TTY (local dev), `JSONRenderer` otherwise (CI, containers).

**Traces.** Lore relies on `opentelemetry-instrument` (from `opentelemetry-distro`) for SDK provider wiring. When the wrapper is used, the auto-config configurator installs SDK `TracerProvider` and `MeterProvider` from `OTEL_*` env vars: OTLP exporters, batch processors, resource detection. `configure_telemetry()` adds the Lore-specific glue against whichever global providers are installed (SDK under the wrapper, API-proxy no-op when bare): structlog with the trace-context processor, and the LiteLLM OTel callback (`litellm.callbacks = ["otel"]`). The tracer is resolved lazily by `start_span()` at first use, so no provider snapshot is taken at configure time. FastMCP picks up the global `TracerProvider` automatically for adapter-level tracing. The orchestrator creates child spans via the module-level `start_span(...)` helper from `lore.telemetry` for each stage of the execution loop (`lore.interpret`, `lore.embed_sources`, `lore.search_candidates`, `lore.enrich`, `lore.reason`, `lore.embed_novels`, `lore.record`). OTel context propagation handles parent-child relationships, so no manual correlation ID passing is required. `trace_id` is injected into structlog log events via a processor so logs and traces share correlation identity.

**Metrics.** Metrics use OTel's `Meter` API directly: `otel_metrics.get_meter("lore").create_counter(name).add(...)` at the call site. No domain-specific convenience methods.

**Env-var contract.** SDK behaviour is configured by the standard OTel Python env vars; Lore reads only the structlog gate. The invariant: SDK wiring precedes everything, wrapper-owned; Lore's glue precedes Lore's emissions, factory-owned. `configure_telemetry()` is the first statement of the `server()` factory, so structlog is up before settings load or any other Lore code emits.

- `OTEL_TRACES_EXPORTER` / `OTEL_METRICS_EXPORTER` / `OTEL_LOGS_EXPORTER`: `otlp` (default when wrapped), `console`, or `none`. Under stdio MCP transport, `console` corrupts the protocol channel: pair console exporters with HTTP transport, or use OTLP.
- `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_HEADERS`: endpoint shipping.
- `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`: collector-side tagging.
- `OTEL_SDK_DISABLED` / `OTEL_TRACES_EXPORTER=none`: off-switches.
- `LOG_LEVEL`: stderr verbosity for structlog. Lore-specific (no `OTEL_` prefix). `configure_telemetry()` mirrors it into `OTEL_LOG_LEVEL` via `os.environ.setdefault` so the OTel SDK's internal log verbosity follows the operator's chosen application level; an explicit `OTEL_LOG_LEVEL` wins.
- `USE_OTEL_LITELLM_REQUEST_SPAN`: litellm knob, defaulted to `true` by `configure_telemetry()` via `os.environ.setdefault`; an explicit operator value wins. In its default mode litellm's async OTel handlers decorate the inherited parent span, which in Lore's stage-span shape has always ended by the time they run: the SDK warns per attribute and records nothing. Request-span mode records each LLM call on its own child span under the stage span.

**`oracle_id` redaction at the collector.** Oracle identity flows through the orchestrator as the raw `oracle_id` string: the IdP's `sub` claim, or the synthetic `_local` for unauthenticated topologies. The same value reaches the ledger and provenance tables (the `oracle_id` column) and telemetry alike (span attributes, structlog bindings). Operators who need `oracle_id` redacted from telemetry configure their OTel collector's `attributes` processor at the export boundary (`action: hash` or `action: delete`); the application stays unchanged. Same pattern as authentication and rate-limiting: concerns pushed to the right layer.

### Authentication

When `OIDC_URL` is configured, the adapter runs the standard MCP OAuth flow with that IdP and extracts the oracle identity from the `sub` claim. Without `OIDC_URL`, the adapter falls back to the synthetic `_local` identity for every request, which is appropriate for stdio dev and for HTTP topologies that authenticate at an upstream proxy. Oracle identity is a plain string on the ledger; there is no dedicated table and no auto-creation step. No layer below the adapter knows about OAuth; the orchestrator receives a verified oracle identity string, not a token.

The `_*` namespace is used by the synthetic identities (`_local`, `_transfer`). IdP-claimed `sub` values pass through verbatim: the IdP is the identity root, and the one name whose collision would matter (`_transfer`, written with full credibility) is refused by the Recorder at the domain layer, where that invariant lives.

**Auth opt-in.** `[auth] required` is the operator-controlled fail-fast. Default `false`: Lore reads the FastMCP env-var contract (`FASTMCP_HOST`, `FASTMCP_TRANSPORT`) and never second-guesses it. Operators who want bootstrap to refuse without OIDC set `required = true` under `[auth]` in `lore.toml`; the missing `OIDC_URL` is then a hard error at startup, raised during settings load before migrations or the server start. The auth↔OIDC pairing is a cross-section invariant on `LoreSettings`, validated as the settings model is built rather than as a separate procedural step. The startup sequence is factory (telemetry → settings, where cross-section invariants including auth↔OIDC are validated) → lifespan (migrations → repos → providers → orchestrator); the refusal fires in the factory before any I/O opens, and telemetry comes first so the refusal itself is logged through structlog.

Three HTTP topologies follow from this:

- **HTTP behind an authenticating proxy** (supported). The proxy terminates OIDC and forwards an upstream-trusted oracle identity; Lore runs without `OIDC_URL` and the synthetic `_local` is the right fallback for the proxy-to-Lore hop.
- **HTTP with OIDC in Lore** (supported). `OIDC_URL` and `BASE_URL` are both set; Lore runs `OIDCProxy` directly and extracts the oracle from the `sub` claim.
- **HTTP with no authentication anywhere** (not a topology). Either terminate auth at the edge or set `required = true` under `[auth]` so startup refuses; running HTTP open is the operator's mistake, not a deployment mode Lore supports.

**Rate limiting.** Rate limiting belongs at the edge proxy; the proxy authenticates the request and is the right vantage point. Lore emits no per-oracle metric for this purpose; replicating per-oracle traffic counters inside the orchestrator would explode metric cardinality on multi-tenant APMs without adding information the proxy doesn't already see.

**Pool-timeout opacity.** Backpressure that does reach Lore (a caller waiting past `timeout` on an exhausted Postgres pool) surfaces as `psycopg_pool.PoolTimeout`, translates to `StorageError`, and exits through the adapter's catch-all as a scrubbed 5xx. The opacity is the contract, not a code smell: per-oracle pool accounting would duplicate the proxy's rate-limit signal at higher cardinality, and exposing the raw exception class to the client would leak deployment topology without giving the caller anything actionable. Operators diagnose pool pressure from structlog and pool-level metrics, not from the wire payload.

---

## Dogfooding

Lore develops Lore. As soon as the MCP tool exists, it runs locally backed by SQLite. Every architectural hypothesis, every contested approach enters Lore's own knowledge base. The development team is the first herd.
