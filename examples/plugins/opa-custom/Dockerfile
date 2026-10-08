# Custom OPA Plugin sidecar (ADR-042). Not the built-in apme-opa validator.
# Build from repo root:
#   podman build -t apme-plugin-opa-custom:latest -f examples/plugins/opa-custom/Dockerfile .
ARG BASE_IMAGE=localhost/apme-base:latest
FROM docker.io/openpolicyagent/opa:1.17.1 AS opa-bin
FROM $BASE_IMAGE

USER root

COPY --from=opa-bin /opa /usr/local/bin/opa
COPY examples/plugins/opa-custom/plugin.py /app/plugin.py
COPY examples/plugins/opa-custom/bundle /bundle
RUN chmod a+rx /usr/local/bin/opa /app/plugin.py \
    && chmod -R a+rX /bundle

USER 1001

ENV APME_PLUGIN_LISTEN=0.0.0.0:50100 \
    APME_OPA_PLUGIN_BUNDLE=/bundle \
    APME_OPA_PLUGIN_ENTRYPOINT=data.apme.plugin.violations
EXPOSE 50100

ENTRYPOINT ["python", "/app/plugin.py"]
