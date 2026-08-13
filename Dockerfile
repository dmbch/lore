# syntax=docker/dockerfile:1

# Base digests are multi-arch index digests, kept current by dependabot.
# Pinned uv binary: must satisfy required-version (pyproject [tool.uv]), which
# hard-errors this build on mismatch; bump both and mise.toml together.
FROM ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc AS uv

FROM python:3.14-slim-trixie@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/lore \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /src

# Dependency layer first: resolve from the lockfile without the project, so a
# source-only change reuses this cached install instead of reinstalling deps.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Project layer: install Lore itself as a non-editable wheel into the venv.
# Prompts (lore/prompts/*.md) and vendor configs ship as package data.
COPY . /src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime

# Baked by the publish job (--build-arg LORE_VERSION=<release>); unset from
# source so create_server reports the "0.0.0+dev" marker instead.
ARG LORE_VERSION=
# FASTMCP_* are operator-overridable image defaults: HTTP transport bound to
# the container interface (the netns is the isolation boundary; an image that
# exposes a port should serve it), no banner, no update check (an on-prem
# image must not phone home).
ENV LORE_VERSION=${LORE_VERSION} \
    PATH=/opt/lore/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/lore.db \
    FASTMCP_TRANSPORT=http \
    FASTMCP_HOST=0.0.0.0 \
    FASTMCP_SHOW_SERVER_BANNER=false \
    FASTMCP_CHECK_FOR_UPDATES=off \
    OTEL_TRACES_EXPORTER=none \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none

LABEL org.opencontainers.image.source="https://github.com/dmbch/lore" \
      org.opencontainers.image.description="Shared knowledge engine for centaur teams" \
      org.opencontainers.image.licenses="MIT"

COPY --from=builder /opt/lore /opt/lore

RUN groupadd --gid 1000 lore \
    && useradd --uid 1000 --gid 1000 --home-dir /data --no-create-home \
       --shell /usr/sbin/nologin lore \
    && install -d -o lore -g lore /data

USER lore
WORKDIR /data
EXPOSE 8000

# opentelemetry-instrument is the always-on wrapper; with exporters set to
# "none" it adds no overhead. `python -m lore` runs the same server() factory
# dev drives via `fastmcp run`; the wheel-only image has no source tree for the
# fastmcp CLI's file spec. Config is optional: mount to /etc/lore.toml or drop
# lore.toml in /data (CWD), per the discovery order in docs/architecture.md.
ENTRYPOINT ["opentelemetry-instrument", "python", "-m", "lore"]
