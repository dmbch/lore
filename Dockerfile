# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14

# Pinned uv binary — match the version that resolved uv.lock (verify on bump).
FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:${PYTHON_VERSION}-slim-trixie AS builder
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

FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

# Baked by the publish job (--build-arg LORE_VERSION=<release>); unset from
# source so create_server reports the "0.0.0+dev" marker instead.
ARG LORE_VERSION=
ENV LORE_VERSION=${LORE_VERSION} \
    PATH=/opt/lore/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/lore.db \
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
# "none" it adds no overhead. Config is optional: mount to /etc/lore.toml or
# drop lore.toml in /data (CWD), per the discovery order in docs/architecture.md.
ENTRYPOINT ["opentelemetry-instrument", "python", "-m", "lore"]
