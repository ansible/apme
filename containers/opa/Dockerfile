# OPA validator: gRPC service; evaluates Rego via ``opa eval`` subprocess.
# Multi-stage: copies the OPA binary from the official image.
ARG BASE_IMAGE=localhost/apme-base:latest
FROM docker.io/openpolicyagent/opa:1.17.1 AS opa-bin
FROM $BASE_IMAGE

USER root

COPY --from=opa-bin /opa /usr/local/bin/opa

RUN cp -r /app/src/apme_engine/validators/opa/bundle /bundle \
    && cp /app/containers/opa/entrypoint.sh /entrypoint.sh \
    && chmod -R a+rX /bundle \
    && chmod a+rx /entrypoint.sh

USER 1001

ENV APME_OPA_VALIDATOR_LISTEN=0.0.0.0:50054
EXPOSE 50054

ENTRYPOINT ["/entrypoint.sh"]
