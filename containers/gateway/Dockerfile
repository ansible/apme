# API Gateway: FastAPI (async) — REST/WebSocket to gRPC translation.
# Multi-stage build: Node stage builds the standalone UI, Python stage runs the gateway.
# No host dependencies beyond Podman — Node.js, pnpm, and Python are all containerized.
#
# Build variants:
#   Default (--with-ui):  podman build --build-arg BUILD_UI=1 ...
#   API-only (no UI):     podman build ...

# ── Stage 1: Build the standalone UI (conditional) ───────────────────────────
FROM node:22-slim AS ui-builder

ARG BUILD_UI=0
WORKDIR /ui

# Copy workspace config
COPY ui/package.json ui/pnpm-workspace.yaml ui/tsconfig.base.json ./

# Copy package manifests for dependency install
COPY ui/shared/package.json shared/package.json
COPY ui/standalone/package.json standalone/package.json
COPY ui/standalone/tsconfig.json standalone/tsconfig.json
COPY ui/shared/tsconfig.json shared/tsconfig.json

# Install pnpm and dependencies (cached layer)
RUN if [ "$BUILD_UI" = "1" ]; then \
      corepack enable && corepack prepare pnpm@latest --activate && \
      pnpm install --frozen-lockfile 2>/dev/null || pnpm install; \
    fi

# Copy source and build
COPY ui/shared/src/ shared/src/
COPY ui/standalone/src/ standalone/src/
COPY ui/standalone/index.html standalone/index.html
COPY ui/standalone/vite.config.ts standalone/vite.config.ts

RUN if [ "$BUILD_UI" = "1" ]; then \
      cd /ui && \
      pnpm --filter @apme/ui-shared build && \
      pnpm --filter @apme/standalone build; \
    else \
      mkdir -p /ui/standalone/dist; \
    fi

# ── Stage 2: Python gateway ─────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies (cached layer)
COPY pyproject.toml README.md ./
COPY src/ src/
COPY proto/ proto/
RUN pip install --no-cache-dir ".[gateway]"

# Copy built UI assets from stage 1 (empty dir if BUILD_UI=0)
COPY --from=ui-builder /ui/standalone/dist/ ui/standalone/dist/

ENV APME_GATEWAY_HOST=0.0.0.0
ENV APME_GATEWAY_PORT=50050
ENV APME_PRIMARY_ADDRESS=127.0.0.1:50051
ENV APME_UI_DIR=/app/ui/standalone/dist
EXPOSE 50050

CMD ["apme-gateway"]
