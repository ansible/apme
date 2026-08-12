# Gateway: gRPC Reporting receiver + FastAPI REST API.
# Persists scan/fix events to PostgreSQL (ADR-020 / ADR-029).
# Project operations require git for on-demand cloning (ADR-037).
ARG BASE_IMAGE=localhost/apme-base:latest
FROM $BASE_IMAGE

USER root
RUN microdnf install -y git && microdnf clean all
USER 1001

ENV APME_GATEWAY_GRPC_LISTEN=0.0.0.0:50060
ENV APME_GATEWAY_HTTP_HOST=0.0.0.0
ENV APME_GATEWAY_HTTP_PORT=8080
EXPOSE 50060 8080

CMD ["apme-gateway"]
