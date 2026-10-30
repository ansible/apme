# Build the checkout's packages, then bake them into a pinned RHDH host.
# No APME release or host Node installation is needed by tox -e build.
ARG NODE_IMAGE=registry.access.redhat.com/ubi10/nodejs-24-minimal:10.2
ARG RHDH_IMAGE=quay.io/rhdh-community/rhdh@sha256:3bd31bddec7e9b37856fa8ae375e1461076c88d287a2cb0bbda86651e4243f49

FROM ${NODE_IMAGE} AS plugins
USER root
WORKDIR /app
COPY package.json package-lock.json tsconfig.json ./
COPY frontend/packages/apme-client/package.json frontend/packages/apme-client/
COPY frontend/packages/ui-workflow/package.json frontend/packages/ui-workflow/
COPY frontend/backstage/plugins/backstage-apme/package.json frontend/backstage/plugins/backstage-apme/
COPY frontend/backstage/plugins/backstage-apme-backend/package.json frontend/backstage/plugins/backstage-apme-backend/
COPY containers/rhdh/plugin/package.json containers/rhdh/plugin/
RUN npm ci
COPY frontend/packages frontend/packages
COPY frontend/backstage frontend/backstage
COPY containers/rhdh/plugin containers/rhdh/plugin
RUN npm run build && node frontend/backstage/scripts/build-rhdh.mjs

FROM ${RHDH_IMAGE}
USER root
WORKDIR /opt/app-root/src
COPY --from=plugins /app/containers/rhdh/plugin/dist-dynamic local-plugins/apme-frontend
COPY --from=plugins /app/frontend/backstage/plugins/backstage-apme-backend/dist-dynamic local-plugins/apme-backend
COPY containers/rhdh/guest-provider local-plugins/guest-provider
COPY containers/rhdh/app-config.yaml containers/rhdh/app-config.yaml
COPY containers/rhdh/dynamic-plugins.yaml dynamic-plugins.yaml
COPY containers/rhdh/entrypoint.sh /entrypoint.sh
# Install at image-build time. Startup needs no registry or package downloads.
RUN ./install-dynamic-plugins.sh dynamic-plugins-root \
    && chmod -R a+rX local-plugins dynamic-plugins-root containers/rhdh \
    && chmod a+rx /entrypoint.sh \
    && chown -R 1001:0 dynamic-plugins-root
USER 1001
EXPOSE 8081
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:8081/.backstage/health/v1/readiness').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
ENTRYPOINT ["/entrypoint.sh"]
