# Backstage app with the APME plugin pre-installed.
# Builds a standalone Backstage instance with the APME frontend + backend plugins.
# No host dependencies — Node.js and yarn are containerized.
#
# Build:  podman build -t apme-backstage:latest -f containers/backstage/Dockerfile .
# Run:    podman run -p 7007:7007 -e APME_GATEWAY_URL=http://host.containers.internal:50050 apme-backstage:latest
#
# This is a self-contained Backstage instance for teams that don't already run Backstage.
# For teams with an existing Backstage, install the plugin packages directly instead.

FROM node:22-slim AS builder

RUN corepack enable && corepack prepare yarn@4 --activate

WORKDIR /app

# Create a minimal Backstage app
RUN npx --yes @backstage/create-app@latest --path /app --skip-install 2>/dev/null || true

# Copy APME plugin source
COPY ui/backstage-plugin/plugins/apme/ plugins/apme/
COPY ui/backstage-plugin/plugins/apme-backend/ plugins/apme-backend/
COPY ui/shared/ plugins/shared/

# Install dependencies
RUN yarn install

# Wire up the APME backend plugin
RUN echo 'backend.add(import("./plugins/apme-backend"));' >> packages/backend/src/index.ts

# Build
RUN yarn build:backend

# ── Production image ─────────────────────────────────────────────────────────
FROM node:22-slim

RUN corepack enable

WORKDIR /app
COPY --from=builder /app /app

ENV APME_GATEWAY_URL=http://localhost:50050

EXPOSE 7007
CMD ["node", "packages/backend", "--config", "app-config.yaml"]
