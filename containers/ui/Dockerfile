# Standalone UI: React SPA served by nginx.
# Talks only to the gateway container — never directly to the engine pod.
# Two-stage build: Node for Vite build, nginx for serving (UBI10, ADR-061).

ARG UBI_NODEJS_IMAGE=registry.access.redhat.com/ubi10/nodejs-22-minimal:10.2
ARG UBI_NGINX_IMAGE=registry.access.redhat.com/ubi10/nginx-126:10.2

# --- Stage 1: Build ---
FROM ${UBI_NODEJS_IMAGE} AS build

USER root
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# --- Stage 2: Serve ---
FROM ${UBI_NGINX_IMAGE}

USER root
RUN dnf install -y gettext && dnf clean all

COPY containers/ui/nginx.conf.template /etc/nginx/conf.d/default.conf.template
COPY containers/ui/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY --from=build /app/dist /usr/share/nginx/html

RUN chmod -R g+w /etc/nginx/conf.d /var/log/nginx /run
USER 1001

EXPOSE 8081
ENTRYPOINT ["/entrypoint.sh"]
