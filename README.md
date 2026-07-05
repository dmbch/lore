# Lore

**As far as we know.**

[![Latest release](https://img.shields.io/github/v/release/dmbch/lore)](https://github.com/dmbch/lore/releases)

Lore is a shared archive for teams that think for a living. It connects [centaurs](https://en.wikipedia.org/wiki/Advanced_chess) (a human and a frontier model working together) into a herd that shares its memory. Contribution is a byproduct of working, never a separate task; the archive grows as the herd uses it.

Knowledge is scored with [Subjective Logic](https://en.wikipedia.org/wiki/Subjective_logic): opinions carry belief, disbelief, and uncertainty, not a binary true/false. Trust is earned, not granted; it accrues by aligning with where the herd lands over time. Evidence decays unless re-attested, so stale claims fade on their own. Being early and right counts for more than agreeing with a settled answer.

Technically: an MCP server with epistemic scoring, trust grading, and temporal decay. PostgreSQL with pgvector for production, SQLite with sqlite-vec for local development. OIDC authentication for multi-user deployments. Run it locally over stdio, or deploy the published container image.

See [IDEA.md](IDEA.md) for the full concept.

## Status

Early development.

## Deployment

Lore ships as one batteries-included image: both backends are present (SQLite + sqlite-vec, PostgreSQL + pgvector), and you choose at runtime via `DATABASE_URL`. Images are published to the GitHub Container Registry as `ghcr.io/dmbch/lore`. Each release publishes `:X.Y.Z`, a `:X.Y` minor track that rolls forward, and `:latest`. The examples below use `:latest`; pin `:X.Y.Z` (or the `:X.Y` track) for production.

The image runs as non-root (UID 1000, user `lore`), keeps state under `/data`, exposes port 8000 for the HTTP transport, and shuts down cleanly on `SIGTERM`. OpenTelemetry is opt-in, with exporters defaulting to `none`; see [Telemetry](#telemetry-optional) to ship traces and metrics.

### Local, single user (stdio)

The default transport is stdio: one user, one process, one machine. SQLite lives at `/data/lore.db`, so a single volume persists everything.

```bash
docker run -i --rm \
  -v lore-data:/data \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  ghcr.io/dmbch/lore:latest
```

Point your MCP client (Claude Desktop, the MCP Inspector, …) at that `docker run` command. A vendor API key must be present at startup so Lore can resolve its embedding model, though no network call is made at boot; resolution is a local cost-map lookup. To run from source instead, see [Development](#development).

### HTTP, multi-user

Switch transports with `FASTMCP_TRANSPORT=http`. The image doesn't set `FASTMCP_HOST`, so FastMCP binds loopback (`127.0.0.1`), unreachable from outside the container. To accept external traffic, set `FASTMCP_HOST=0.0.0.0` and publish the port.

```bash
docker run --rm \
  -v lore-data:/data \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e FASTMCP_TRANSPORT=http \
  -e FASTMCP_HOST=0.0.0.0 \
  -e FASTMCP_PORT=8000 \
  -p 8000:8000 \
  ghcr.io/dmbch/lore:latest
```

The HTTP transport adds two operator endpoints alongside the MCP path: `GET /health` (liveness) and `GET /ready` (readiness, which confirms a working database connection). Wire them to your load balancer or orchestrator probes.

Running HTTP with no authentication anywhere is not a supported topology. Either terminate auth at an upstream proxy, or enable OIDC in Lore (below) and set `required = true` under `[auth]` in `lore.toml` so startup refuses the open path.

### PostgreSQL

For production, bring your own PostgreSQL with the `pgvector` extension and point `DATABASE_URL` at it; the backend is selected from the DSN scheme.

```bash
docker run --rm \
  -e DATABASE_URL="postgresql://user:pass@db.internal:5432/lore" \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e FASTMCP_TRANSPORT=http -e FASTMCP_HOST=0.0.0.0 \
  -p 8000:8000 \
  ghcr.io/dmbch/lore:latest
```

Migrations run at startup. The bootstrap health check refuses to start on an embedding-model or full-text-config mismatch, so a misconfigured vector space fails fast instead of degrading silently.

### OIDC authentication

For HTTP multi-user with Lore terminating auth itself, set both `OIDC_URL` and `BASE_URL`. `OIDC_URL` is the IdP's full discovery-document URL (ending in `/.well-known/openid-configuration`) with the client credentials in the userinfo. Lore fetches that URL as-is and appends nothing, so the path must be complete.

```bash
docker run --rm \
  -e DATABASE_URL="postgresql://…" \
  -e OIDC_URL="oidc://client_id:secret@auth.example.com/realms/lore/.well-known/openid-configuration" \
  -e BASE_URL="https://lore.example.com" \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e FASTMCP_TRANSPORT=http -e FASTMCP_HOST=0.0.0.0 \
  -p 8000:8000 \
  ghcr.io/dmbch/lore:latest
```

Oracle identity comes from the IdP `sub` claim. Without `OIDC_URL`, every request runs as the synthetic `_local` identity, correct for stdio and for HTTP behind a proxy that authenticates upstream.

Query parameters on `OIDC_URL` (e.g. `?hd=example.com`) are forwarded verbatim to the upstream authorize endpoint.

### Reference deployment (Fly.io)

The dogfooding deployment runs on [Fly.io](https://fly.io): one machine, HTTP transport with OIDC, SQLite on a persistent volume, Gemini for inference, OTLP export to a collector. It is one declarative `fly.toml` plus a handful of secrets: non-secret topology in `[env]`, everything with a credential in `fly secrets`.

```toml
# fly.toml
app = 'lore'
primary_region = 'fra'

[build]
  image = 'ghcr.io/dmbch/lore:0.2.0'

[env]
  BASE_URL = "https://lore.fly.dev"
  FASTMCP_HOST = "0.0.0.0"
  FASTMCP_TRANSPORT = "http"
  OTEL_EXPORTER_OTLP_ENDPOINT = "https://otlp.example.com/otlp"

[mounts]
  source = "lore_data"
  destination = "/data"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = 'stop'
  auto_start_machines = true
  min_machines_running = 1
  processes = ['app']

[[vm]]
  size = "shared-cpu-2x"
  memory = "1gb"
```

The rest are set as Fly secrets rather than `[env]`: the database DSN, the vendor key, the OIDC credentials, and the OTLP auth token:

```bash
fly secrets set \
  DATABASE_URL="sqlite:////data/lore.db" \
  GEMINI_API_KEY="…" \
  OIDC_URL="oidc://client_id:client_secret@accounts.google.com/.well-known/openid-configuration?hd=example.com" \
  OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20…"

fly volumes create lore_data --region fra --size 1
fly deploy
```

A few Lore-specific notes:

- **`FASTMCP_HOST = "0.0.0.0"` is mandatory here.** Lore inherits FastMCP's loopback default; leave it and Fly's proxy can't reach the machine, so every request times out.
- **`BASE_URL` and `OIDC_URL` are a validated pair**: Lore refuses to start with one but not the other. `BASE_URL` is the public origin the IdP redirects back to; `OIDC_URL` points at the IdP's discovery document with the client credentials in the userinfo. The `?hd=example.com` query rides through to the authorize endpoint, restricting sign-in to a single Google Workspace domain. Drop both to run behind a proxy that authenticates upstream: oracle identity then falls back to `_local`.
- **`OTEL_EXPORTER_OTLP_HEADERS` is percent-encoded.** The space in `Basic <token>` must be written `%20`; a literal space breaks the header parse.
- **One machine, because SQLite is single-writer.** To scale horizontally, move to Postgres: point `DATABASE_URL` at a `postgresql://…` DSN (still a secret: it carries credentials) and drop the volume mount. Schema and vector space carry over untouched.
- **Wire `GET /ready` to Fly's health checks** so a machine that can't reach its database is pulled from rotation rather than serving failures.

### Configuration file

Behavioral config is TOML, discovered from `./lore.toml` then `/etc/lore.toml` (first found wins). Since `WORKDIR` is `/data`, the simplest path is to drop `lore.toml` into the data volume; otherwise bind-mount a single file:

```bash
docker run … \
  --mount type=bind,src="$PWD/lore.toml",dst=/etc/lore.toml,ro \
  ghcr.io/dmbch/lore:latest
```

### Custom image

To bake in your own `lore.toml` or prompt templates, build a child image from the published one and point `[prompts]` at the copied files.

```dockerfile
FROM ghcr.io/dmbch/lore:0.1.0
COPY lore.toml /etc/lore.toml
COPY prompts/ /opt/lore-prompts/
```

```toml
# lore.toml
[prompts]
archivist = "/opt/lore-prompts/archivist.md"
```

Pin the base image rather than `:latest` so the derived artifact is reproducible, with your configuration baked into a versioned image instead of mounted at runtime.

## Configuration

Configuration has two disjoint sources. Secrets and deployment topology come from environment variables; behavioral config comes from `lore.toml`, discovered from `./lore.toml` then `/etc/lore.toml` (first found wins). Every TOML field has a bundled default, so you override only what you change.

### Environment variables

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Database connection (`postgresql://…` for Postgres, `sqlite:///…` for SQLite) |
| `OIDC_URL` | Full OIDC discovery-document URL with embedded credentials (`oidc://client_id:secret@host/.well-known/openid-configuration`) |
| `BASE_URL` | Public base URL (required with `OIDC_URL` for HTTP mode) |
| `LOG_LEVEL` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `FASTMCP_PORT` | Server port (default 8000, managed by FastMCP) |
| `FASTMCP_HOST` | Server host (default 127.0.0.1, managed by FastMCP) |

`FASTMCP_HOST` defaults to `127.0.0.1` (loopback only), the right shape for stdio and for HTTP behind a same-host proxy. Container deployments that accept traffic from outside the container must set `FASTMCP_HOST=0.0.0.0`.

### Vendor API keys

Lore auto-detects the LLM vendor from API keys in the environment; first lexical match wins (Bedrock before Gemini, Gemini before OpenAI). Any model string [LiteLLM](https://docs.litellm.ai/docs/providers) supports works, and TOML overrides are per-role, so you can mix vendors.

| Vendor | Required env var |
|--------|------------------|
| Gemini | `GEMINI_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Bedrock | `AWS_BEARER_TOKEN_BEDROCK` (long-term Bedrock API key) |

<details>
<summary>Vendor model defaults per role</summary>

| Vendor | Embedding | Fast | Reasoning |
|--------|-----------|------|-----------|
| Gemini | `gemini-embedding-001` | `gemini-flash-lite-latest` | `gemini-flash-latest` |
| OpenAI | `text-embedding-3-small` | `gpt-4.1-mini` | `o4-mini` |
| Bedrock | `titan-embed-text-v2:0` | `nova-2-lite-v1:0` | `nova-2-pro-preview-20251202-v1:0` |

</details>

### Behavioral config (`lore.toml`)

A minimal drop-in. Every field has a bundled default, so override only what you tune to your field; the tables below are the full reference.

```toml
# lore.toml

[epistemics]
attestation_half_life = "90d"
trust_half_life = "90d"
maturity_k = 1.0

[retrieval]
proximity = 0.5
authority = 0.5
limit = 10

[auth]
required = false

[sqlite]
fulltext_config = "porter unicode61"
```

<details>
<summary>Model roles: <code>[embedding]</code>, <code>[fast]</code>, <code>[reasoning]</code></summary>

**`[embedding]`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | vendor default | LiteLLM model string for embeddings |
| `dimensions` | int or omit | resolved from LiteLLM model info | Output vector size. Override for Matryoshka truncation |
| `task_type.document` | string or omit | vendor default | Task type for document embedding |
| `task_type.question` | string or omit | vendor default | Task type for question embedding |
| `task_type.verification` | string or omit | vendor default | Task type for verification embedding |

**`[fast]`**: the Interpreter (fast, cheap)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | vendor default | LiteLLM model string |
| `temperature` | float or omit | `0.0` | Sampling temperature. The Interpreter is a mechanical stage; it runs cold by default |
| `max_tokens` | int or omit | none | Max output tokens |
| `reasoning_effort` | string or omit | vendor default | Reasoning effort level |

**`[reasoning]`**: the Archivist (slow, careful)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | vendor default | LiteLLM model string |
| `temperature` | float or omit | none | Sampling temperature |
| `max_tokens` | int or omit | none | Max output tokens |
| `reasoning_effort` | string or omit | vendor default | Reasoning effort level |

</details>

<details>
<summary>Epistemics: <code>[epistemics]</code>, <code>[retrieval]</code></summary>

**`[epistemics]`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `attestation_half_life` | duration | `"90d"` | How fast knowledge ages. Duration string: `"1y"`, `"3M"`, `"90d"`, `"24h"`, `"60m"`, `"3600s"` |
| `trust_half_life` | duration | `"90d"` | How fast oracle track records age. Independent of attestation decay |
| `maturity_k` | float | `1.0` | Half-saturation constant K for oracle diversity. Higher means more oracles needed before the trust discount lifts. K = 0 disables the maturity safeguard |
| `transfer_threshold` | float > 0 | `1e-3` | Epistemic-significance floor for the consolidated transfer attestation. Fused magnitudes below this skip the transfer row |

**`[retrieval]`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `proximity` | float [0, 1] | `0.5` | Weight for the vector cosine similarity lane |
| `authority` | float [0, 1] | `0.5` | Weight for the full-text search lane |
| `limit` | int | `10` | Final result count after scoring |
| `fan_out` | int | `2` | Multiplier for per-lane candidate fetch (limit x fan_out) |
| `max_keywords` | int | `10` | Max keywords for the authority lane query. Deliberately above the interpreter prompt's 8-keyword ceiling: headroom, not a mismatch |

</details>

<details>
<summary>Storage backends: <code>[postgres]</code>, <code>[sqlite]</code></summary>

Only the section matching your `DATABASE_URL` backend applies.

**`[postgres]`**: connection pool tuning for the production backend

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min_size` | int | `1` | Minimum pooled connections |
| `max_size` | int | `20` | Maximum pooled connections (must be >= `min_size`) |
| `timeout` | float | `10.0` | Seconds a caller waits for a free connection before timing out |
| `max_waiting` | int | `50` | Max callers queued for a connection (0 = unbounded) |
| `fulltext_config` | string | `"english"` | Postgres text-search config for the authority lane (`german`, `french`, `simple`, …) |

**`[sqlite]`**: full-text tokenizer for the development backend

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `fulltext_config` | string | `"porter unicode61"` | FTS5 `tokenize=` spec. Use `"unicode61"` (no stemming) for non-English deployments |

Changing `fulltext_config` on an existing database requires rebuilding the FTS index; the bootstrap health check refuses to start on mismatch.

</details>

<details>
<summary>Server, auth, limits, prompts: <code>[server]</code>, <code>[auth]</code>, <code>[limits]</code>, <code>[prompts]</code></summary>

**`[server]`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `"Lore"` | Server identity for the MCP adapter |
| `icon_url` | string or omit | bundled logo | Logo URL shown on the OIDC consent screen |

**`[auth]`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `required` | bool | `false` | Refuse HTTP startup when no `OIDC_URL` is set: fail-fast for the open-path mistake |
| `verify_id_token` | bool | `true` | Verify the OIDC id_token signature |

**`[limits]`**: character limits for pipeline payloads; all values > 0

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `question` | int | `1024` | Max characters for the question field |
| `hypothesis` | int | `3072` | Max characters for the hypothesis field |
| `context` | int | `4096` | Max characters for the context field |
| `reasoning` | int | `4096` | Max characters for the reasoning field |

**`[prompts]`**: template paths; bundled defaults unless overridden. Each value is a filesystem path or a `bundled:pkg/name.md` reference.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `narrative` | path or omit | omitted | Optional preamble prepended to the core reasoning prompts (interpreter and archivist): house voice, mission |
| `glossary` | path or omit | omitted | Optional glossary prepended to the core reasoning prompts (interpreter and archivist): domain jargon |
| `scribe` | path | bundled | Scribe system prompt |
| `consult` | path | bundled | Consult tool description |
| `interpreter` | path | bundled | Interpreter system prompt |
| `archivist` | path | bundled | Archivist system prompt |

</details>

### Telemetry (optional)

Lore uses the OpenTelemetry Python SDK but ships nothing by default. Bare `lore` records into the OTel API proxies: no spans, no metrics, zero overhead. To export, launch through the auto-config wrapper `opentelemetry-instrument`, which installs SDK providers from `OTEL_*` variables and picks up any installed `opentelemetry-instrumentation-*` packages. The container entrypoint already wraps Lore this way, with every exporter defaulting to `none`; set the standard variables to ship:

```bash
docker run … \
  -e OTEL_TRACES_EXPORTER=otlp \
  -e OTEL_METRICS_EXPORTER=otlp \
  -e OTEL_EXPORTER_OTLP_ENDPOINT="http://collector:4317" \
  ghcr.io/dmbch/lore:latest
```

<details>
<summary>OpenTelemetry environment variables</summary>

| Variable | Purpose |
|----------|---------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector endpoint when shipping via OTLP |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` (default) or `http/protobuf` |
| `OTEL_EXPORTER_OTLP_HEADERS` | Comma-separated headers (e.g. auth tokens) |
| `OTEL_SERVICE_NAME` | Service name attached to spans and metrics |
| `OTEL_RESOURCE_ATTRIBUTES` | Comma-separated `k=v` pairs for collector-side tagging |
| `OTEL_TRACES_EXPORTER` | `otlp` (default), `console`, or `none` |
| `OTEL_METRICS_EXPORTER` | `otlp` (default), `console`, or `none` |
| `OTEL_SDK_DISABLED` | `true` disables the entire SDK |

See the [OTel Python SDK environment variables reference](https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/) for the full surface.

</details>

#### Redacting `oracle_id`

Lore emits `oracle_id` (the IdP `sub` claim) on spans and structured logs without transformation. To redact it before it reaches a third-party APM, configure the OTel collector's `attributes` processor at the export boundary:

```yaml
processors:
  attributes/redact:
    actions:
      - key: oracle_id
        action: hash   # or: action: delete
```

The ledger and provenance tables always store the raw value; redaction applies only to telemetry export.

## Development

### Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)

### Setup

```bash
uv sync
```

### Quality checks

```bash
uv run pytest                # tests
uv run ruff check .          # lint
uv run ruff format --check . # format (read-only)
uv run pyright               # type check
```

### Running from source

```bash
DATABASE_URL=sqlite:////tmp/lore-dev.db uv run lore
```

Bare `uv run lore` records into no-op OTel providers. To ship traces, metrics, and logs to a collector, launch through the auto-config wrapper:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:18889 \
DATABASE_URL=sqlite:////tmp/lore-dev.db \
uv run opentelemetry-instrument lore
```

To step through tool calls, wrap the command with the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) (needs Node.js):

```bash
DATABASE_URL=sqlite:////tmp/lore-dev.db npx @modelcontextprotocol/inspector uv run lore
```

For local OTLP traces, logs, and metrics, the [Aspire Dashboard](https://learn.microsoft.com/en-us/dotnet/aspire/fundamentals/dashboard/standalone) runs as a single container:

```bash
docker run -d --rm \
  --name lore-aspire-dashboard \
  -p 18888:18888 -p 18889:18889 \
  -e DOTNET_DASHBOARD_UNSECURED_ALLOW_ANONYMOUS=true \
  mcr.microsoft.com/dotnet/aspire-dashboard:latest
```

Launch Lore through `opentelemetry-instrument` with `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:18889`, then open http://localhost:18888. Stop with `docker stop lore-aspire-dashboard`.

### Workflow

Lore is developed as a centaur: a human programmer and Claude Code in close collaboration. The workflow lives in [CLAUDE.md](CLAUDE.md), enforced through Claude Code skills and commands. Non-trivial work follows brainstorm, `/plan`, `/build` (TDD), `/review`.

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) (no scope). Releases are cut automatically from those commits by `.github/workflows/release.yml`: the version is computed from commit history, and the same checks that gate every PR (plus end-to-end and container smoke tests) must pass against the released commit before anything ships.

### Architecture

Five layers, hexagonal. See [docs/architecture.md](docs/architecture.md) for the design and [docs/logic.md](docs/logic.md) for the Subjective Logic formalism.

## Contributing

MIT licensed. Open source, but this project doesn't currently solicit or accept pull requests. Issues are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) for how to help, and the [Code of Conduct](CODE_OF_CONDUCT.md) for the ground rules. Found a vulnerability? Don't open a public issue; follow the [security policy](SECURITY.md).

This is an experiment: open source in the age of AI.

## License

MIT
