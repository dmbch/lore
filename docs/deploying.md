# Deploying Lore

Operator guide for running Lore as a container. For the *meaning* of every
configuration variable, see the [Configuration reference in the
README](../README.md#configuration); this guide covers *how* to pass them in
each deployment shape.

Lore ships as a single batteries-included image — both backends (SQLite +
sqlite-vec, PostgreSQL + pgvector) are present, and you choose at runtime via
`DATABASE_URL`. Images are published to the GitHub Container Registry:

```
ghcr.io/dmbch/lore:<version>
```

## Quick start — local, single user (stdio)

The default transport is stdio: one user, one process, local machine. The image
defaults to SQLite at `/data/lore.db`, so a single volume persists everything:

```bash
docker run -i --rm \
  -v lore-data:/data \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  ghcr.io/dmbch/lore:0.1.0
```

`-i` keeps stdin open for the MCP stdio transport; point your MCP client (Claude
Desktop, the MCP Inspector, …) at that `docker run` command. A vendor API key
must be *present* at startup so Lore can resolve its embedding model — no
network call is made at boot. See [Vendor API
Keys](../README.md#vendor-api-keys) for the supported vendors.

## Networked — HTTP (multi-user)

Switch transports with `FASTMCP_TRANSPORT=http`. **The image does not set
`FASTMCP_HOST`, so FastMCP binds loopback (`127.0.0.1`) — unreachable from
outside the container.** To accept external traffic set `FASTMCP_HOST=0.0.0.0`
and publish the port:

```bash
docker run --rm \
  -v lore-data:/data \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e FASTMCP_TRANSPORT=http \
  -e FASTMCP_HOST=0.0.0.0 \
  -e FASTMCP_PORT=8000 \
  -p 8000:8000 \
  ghcr.io/dmbch/lore:0.1.0
```

The HTTP transport exposes two operator endpoints alongside the MCP path: `GET
/health` (liveness) and `GET /ready` (readiness — confirms a working database
connection). Wire them to your load balancer / orchestrator probes.

> Running HTTP with no authentication anywhere is not a supported topology.
> Either terminate auth at an upstream proxy, or enable OIDC in Lore (below) —
> and set `auth_required = true` in `lore.toml` to make startup refuse the open
> path.

## Configuration file

Behavioral config is TOML, discovered from `./lore.toml` then `/etc/lore.toml`
(first found wins). Since `WORKDIR` is `/data`, the simplest path is to drop
`lore.toml` into the data volume; alternatively bind-mount a single file:

```bash
docker run … \
  --mount type=bind,src="$PWD/lore.toml",dst=/etc/lore.toml,ro \
  ghcr.io/dmbch/lore:0.1.0
```

Use `--mount` rather than `-v` for a single file: if the source path is missing,
`-v` silently creates a *directory* there and your config vanishes. The full
field reference is the [TOML section of the README](../README.md#toml).

## PostgreSQL

For production, bring your own PostgreSQL with the `pgvector` extension and
point `DATABASE_URL` at it — the backend is selected from the DSN scheme:

```bash
docker run --rm \
  -e DATABASE_URL="postgresql://user:pass@db.internal:5432/lore" \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e FASTMCP_TRANSPORT=http -e FASTMCP_HOST=0.0.0.0 \
  -p 8000:8000 \
  ghcr.io/dmbch/lore:0.1.0
```

Migrations run automatically at startup; the bootstrap health check refuses to
start on an embedding-model or full-text-config mismatch, so a misconfigured
vector space fails fast rather than degrading silently.

## OIDC authentication

For HTTP multi-user with Lore terminating auth itself, set both `OIDC_URL` (the
discovery URL with embedded client credentials) and `BASE_URL`:

```bash
docker run --rm \
  -e DATABASE_URL="postgresql://…" \
  -e OIDC_URL="oidc://client_id:secret@auth.example.com/realms/lore" \
  -e BASE_URL="https://lore.example.com" \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e FASTMCP_TRANSPORT=http -e FASTMCP_HOST=0.0.0.0 \
  -p 8000:8000 \
  ghcr.io/dmbch/lore:0.1.0
```

Oracle identity is taken from the IdP `sub` claim. Without `OIDC_URL` every
request runs as the synthetic `_local` identity — correct for stdio, and for
HTTP behind a proxy that authenticates upstream.

## OpenTelemetry

The entrypoint is always `opentelemetry-instrument`, but the image defaults all
exporters to `none` — zero overhead until you opt in. To ship, set the standard
`OTEL_*` variables:

```bash
docker run … \
  -e OTEL_TRACES_EXPORTER=otlp \
  -e OTEL_METRICS_EXPORTER=otlp \
  -e OTEL_EXPORTER_OTLP_ENDPOINT="http://collector:4317" \
  ghcr.io/dmbch/lore:0.1.0
```

See [Telemetry](../README.md#telemetry-optional) for the full variable surface
and the `oracle_id` redaction pattern.

## Customizing the image

To bake in your own `lore.toml` or prompt templates, build a child image `FROM`
the published one and point `[prompts]` at the copied files:

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

This keeps the published image immutable while pinning your configuration into a
derived, versioned artifact.

## Resource shape

- **User:** non-root, UID 1000 (`lore`).
- **Volume:** `/data` — SQLite database and state.
- **Port:** 8000 — HTTP transport (`EXPOSE`d; published only if you `-p`).
- **Signals:** `SIGTERM` shuts the server down cleanly.

Payload character limits (`[limits]`) and the epistemic hyperparameters
(`[decay]`, `[trust]`, `[retrieval]`) that tune Lore to your field both live in
`lore.toml`; see the [TOML reference](../README.md#toml).
