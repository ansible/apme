# syntax=docker/dockerfile:1
# Shared base for all Python APME services.
# Installs core dependencies once; service images inherit via FROM apme-base.
# Base OS: UBI10 (ADR-061).
ARG UBI_PYTHON_IMAGE=registry.access.redhat.com/ubi10/python-312-minimal:10.2
FROM ${UBI_PYTHON_IMAGE}

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /usr/local/bin/

USER root

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app

# Dependency layer: only invalidated when pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-install-project --no-dev \
            --extra ai --extra gateway

# Full source; install the project itself (deps already cached).
COPY . /app
RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --extra ai --extra gateway

ENV PATH="/app/.venv/bin:$PATH"

USER 1001
