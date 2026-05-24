# Lore

**As far as we know.**

[![CI](https://github.com/dmbch/lore/actions/workflows/ci.yml/badge.svg)](https://github.com/dmbch/lore/actions/workflows/ci.yml)
[![Release](https://github.com/dmbch/lore/actions/workflows/release.yml/badge.svg)](https://github.com/dmbch/lore/actions/workflows/release.yml)
[![Commitlint](https://github.com/dmbch/lore/actions/workflows/commitlint.yml/badge.svg)](https://github.com/dmbch/lore/actions/workflows/commitlint.yml)

Lore is a shared archive for teams that think for a living. It connects centaurs (a human and a frontier model, working together) into a herd that shares its memory. Contribution is a byproduct of working, never a separate task. The commons grows with use.

Knowledge is scored using [Subjective Logic](https://en.wikipedia.org/wiki/Subjective_logic) -- opinions expressed as belief, disbelief, and uncertainty rather than binary true/false. Trust is not granted; it is earned through alignment with the herd over time. Knowledge decays unless re-attested. Dissent is priced honestly: being early and right earns more than rubber-stamping a settled answer.

Technically: an MCP server with epistemic scoring, trust grading, and temporal decay. PostgreSQL with pgvector for production, SQLite with sqlite-vec for development. OIDC authentication for multiuser deployments. Runs locally over stdio, or as a published container image for self-hosting — see [docs/deploying.md](docs/deploying.md).

See [IDEA.md](IDEA.md) for the full concept.

## Status

Early development.

## Deployment

Pull the published image and run it locally over stdio:

```bash
docker run -i --rm -v lore-data:/data -e GEMINI_API_KEY=… ghcr.io/dmbch/lore:0.1.0
```

For HTTP / multi-user, PostgreSQL, OIDC, OpenTelemetry, and image customization, see [docs/deploying.md](docs/deploying.md). How releases are cut and versioned: [docs/release.md](docs/release.md).

## Configuration

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Database connection (`postgresql://…` for Postgres, `sqlite:///…` for SQLite) |
| `OIDC_URL` | OIDC discovery URL with embedded credentials (`oidc://client_id:secret@host/path`) |
| `BASE_URL` | Public base URL (required with `OIDC_URL` for HTTP mode) |
| `LOG_LEVEL` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `FASTMCP_PORT` | Server port (default 8000, managed by FastMCP) |
| `FASTMCP_HOST` | Server host (default 127.0.0.1, managed by FastMCP) |

The `FASTMCP_HOST` default of `127.0.0.1` is loopback-only — the right shape for stdio and for HTTP behind a same-host proxy. Container deployments that need to accept traffic from outside the container must set `FASTMCP_HOST=0.0.0.0`.

OpenTelemetry shipping is opt-in via `opentelemetry-instrument`; see [Telemetry (optional)](#telemetry-optional) below for the env-var surface.

### Vendor API Keys

Lore auto-detects which LLM vendor to use from API keys in the environment. First lexical match wins (Bedrock is checked before Gemini, Gemini before OpenAI). TOML overrides are per-role -- you can mix vendors. Any model string supported by [LiteLLM](https://docs.litellm.ai/docs/providers) works.

| Vendor | Required env var(s) |
|--------|---------------------|
| Gemini | `GEMINI_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Bedrock | `AWS_BEARER_TOKEN_BEDROCK` (long-term Bedrock API key) |

Vendor defaults per model role:

| Vendor | Embedding | Fast | Reasoning |
|--------|-----------|------|-----------|
| Gemini | `gemini-embedding-001` | `gemini-flash-lite-latest` | `gemini-flash-latest` |
| OpenAI | `text-embedding-3-small` | `gpt-4.1-mini` | `o4-mini` |
| Bedrock | `titan-embed-text-v2:0` | `nova-2-lite-v1:0` | `nova-2-pro-preview-20251202-v1:0` |

### Telemetry (optional)

Lore uses the OpenTelemetry Python SDK, but ships no telemetry by default. Bare `uv run lore` records into the OTel API proxies — no spans, no metrics, zero overhead. To ship spans, metrics, and logs to a collector, launch via the auto-config wrapper instead: `uv run opentelemetry-instrument lore`. The wrapper installs SDK providers configured from `OTEL_*` environment variables, picks up any installed `opentelemetry-instrumentation-*` packages (psycopg, FastMCP, etc.), and ships through whichever exporter the env vars name.

| Variable | Purpose |
|----------|---------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector endpoint when shipping via OTLP |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` (default) or `http/protobuf` |
| `OTEL_EXPORTER_OTLP_HEADERS` | Comma-separated headers (e.g. auth tokens) |
| `OTEL_SERVICE_NAME` | Service name attached to spans and metrics |
| `OTEL_RESOURCE_ATTRIBUTES` | Comma-separated `k=v` pairs for collector-side tagging |
| `OTEL_TRACES_EXPORTER` | `otlp` (default), `console`, or `none` |
| `OTEL_METRICS_EXPORTER` | `otlp` (default), `console`, or `none` |
| `OTEL_SDK_DISABLED` | `true` disables the entire SDK; alternative off-switch |

See the [OTel Python SDK environment variables reference](https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/) for the full surface.

#### Redacting sensitive attributes

Lore emits `oracle_id` (the IdP `sub` claim) on spans and structured logs without transformation. Operators who need it redacted before it reaches a third-party APM configure the OTel collector's `attributes` processor at the export boundary -- the standard OTel pattern, not an application concern:

```yaml
processors:
  attributes/redact:
    actions:
      - key: oracle_id
        action: hash   # or: action: delete
```

The ledger and provenance tables always store the raw value; redaction applies only to telemetry export.

### TOML

Behavioral config lives in `lore.toml` (discovered from `./lore.toml` then `/etc/lore.toml`; first found wins). All fields have bundled defaults -- a deployer only needs to override what they want to change.

#### `[server]`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `"Lore"` | Server identity for the MCP adapter |

#### `[embedding]`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | vendor default | [LiteLLM model string](https://docs.litellm.ai/docs/providers) for embeddings |
| `dimensions` | int or omit | resolved from LiteLLM model info | Output vector size. Override for Matryoshka truncation |
| `task_type.document` | string or omit | vendor default | Vendor-specific task type for document embedding |
| `task_type.question` | string or omit | vendor default | Vendor-specific task type for question embedding |
| `task_type.verification` | string or omit | vendor default | Vendor-specific task type for verification embedding |

#### `[fast]`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | vendor default | LiteLLM model string for the Interpreter (fast, cheap) |
| `temperature` | float or omit | -- | Sampling temperature |
| `max_tokens` | int or omit | -- | Max output tokens |
| `reasoning_effort` | string or omit | vendor default | Reasoning effort level |

#### `[reasoning]`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | vendor default | LiteLLM model string for the Archivist (slow, careful) |
| `temperature` | float or omit | -- | Sampling temperature |
| `max_tokens` | int or omit | -- | Max output tokens |
| `reasoning_effort` | string or omit | vendor default | Reasoning effort level |

#### `[decay]`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `attestation` | duration | `"90d"` | How fast knowledge ages. Duration string: `"1y"`, `"3M"`, `"90d"`, `"24h"`, `"60m"`, `"3600s"` |
| `trust` | duration | `"90d"` | How fast oracle track records age. Independent of attestation decay |

#### `[trust]`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `maturity` | float | `1.0` | Half-saturation constant K for oracle diversity. Higher = more oracles needed before the trust discount lifts. K = 0 disables the maturity safeguard |
| `threshold` | float > 0 | `1e-3` | Epistemic-significance floor for the consolidated transfer attestation. Fused magnitudes below this value skip the transfer row — the algebra has nothing meaningful to carry over |

#### `[retrieval]`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `proximity` | float [0, 1] | `0.5` | Weight for vector cosine similarity lane |
| `authority` | float [0, 1] | `0.5` | Weight for full-text search lane |
| `limit` | int | `10` | Final result count after scoring |
| `fan_out` | int | `2` | Multiplier for per-lane candidate fetch (limit x fan_out) |
| `max_keywords` | int | `10` | Max keywords for the authority lane query |

#### `[limits]`

Character limits for pipeline payloads. All values must be > 0.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `question` | int | `1024` | Max characters for the question field |
| `hypothesis` | int | `3072` | Max characters for the hypothesis field |
| `context` | int | `4096` | Max characters for the context field |
| `reasoning` | int | `4096` | Max characters for the reasoning field |
| `answer` | int | `8192` | Max characters for the Archivist's answer |

#### `[prompts]`

Prompt template paths. Bundled defaults are used unless overridden.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `scribe` | path | bundled | Scribe system prompt |
| `consult` | path | bundled | Consult tool description |
| `interpreter` | path | bundled | Interpreter system prompt |
| `archivist` | path | bundled | Archivist system prompt |


## Development

### Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (package manager)

### Setup

```bash
uv sync
```

### Quality Checks

```bash
uv run pytest                # tests
uv run ruff check .          # lint
uv run ruff format --check . # format (read-only check)
uv run pyright               # type check
```

### Running locally

```bash
DATABASE_URL=sqlite:////tmp/lore-dev.db uv run lore
```

Bare `uv run lore` records into no-op OTel providers — zero telemetry overhead. To ship spans, metrics, and logs to a collector, launch through the OTel auto-config wrapper:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:18889 \
DATABASE_URL=sqlite:////tmp/lore-dev.db \
uv run opentelemetry-instrument lore
```

To step through tool calls interactively, wrap with the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) (requires Node.js):

```bash
DATABASE_URL=sqlite:////tmp/lore-dev.db npx @modelcontextprotocol/inspector uv run lore
```

For local OTLP traces, logs, and metrics, the [Aspire Dashboard](https://learn.microsoft.com/en-us/dotnet/aspire/fundamentals/dashboard/standalone) runs as a single container (requires Docker):

```bash
docker run -d --rm \
  --name lore-aspire-dashboard \
  -p 18888:18888 -p 18889:18889 \
  -e DOTNET_DASHBOARD_UNSECURED_ALLOW_ANONYMOUS=true \
  mcr.microsoft.com/dotnet/aspire-dashboard:latest
```

Then launch Lore through the `opentelemetry-instrument` wrapper with `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:18889` set (see the wrapped form above — setting the endpoint without the wrapper is a no-op). Open http://localhost:18888. Stop with `docker stop lore-aspire-dashboard`.

### Workflow

This project is developed as a centaur: a human programmer and Claude Code working in close collaboration. The workflow is documented in [CLAUDE.md](CLAUDE.md) and enforced through Claude Code skills and commands.

Non-trivial work follows: brainstorm, `/plan`, `/build` (TDD), `/review`.

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) (no scope).

### Architecture

Five layers, hexagonal style. See [docs/architecture.md](docs/architecture.md).

## Contributing

MIT licensed. Open source, but this project does not currently solicit or accept pull requests. Issues are welcome.

This is an experiment: open source in the age of AI.

## License

MIT
