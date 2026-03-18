# OPA validator: gRPC wrapper over OPA REST API.
# Multi-stage: copies the OPA binary from the official image into a Python image
# that runs our gRPC wrapper service.  The wrapper translates Validate RPCs into
# OPA REST queries against localhost:8181.
FROM docker.io/openpolicyagent/opa:latest AS opa-bin

FROM python:3.12-slim

COPY --from=opa-bin /opa /usr/local/bin/opa

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

COPY src/apme_engine/validators/opa/bundle /bundle
COPY containers/opa/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV APME_OPA_VALIDATOR_LISTEN=0.0.0.0:50054
EXPOSE 50054

ENTRYPOINT ["/entrypoint.sh"]
