# Standalone UI: React SPA served by nginx.
# Talks only to the gateway container — never directly to the engine pod.
# Two-stage build: Node for Vite build, nginx for serving.

# --- Stage 1: Build ---
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --ignore-scripts
COPY frontend/ .
RUN npm run build

# --- Stage 2: Serve ---
FROM nginx:1-alpine
COPY containers/ui/nginx.conf.template /etc/nginx/conf.d/default.conf.template
COPY containers/ui/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8081
ENTRYPOINT ["/entrypoint.sh"]
