# CLI: calls Engine over gRPC for check/remediate, runs format locally.
# Mount project at /workspace; Podman overrides APME_ENGINE_ADDRESS to loopback.
ARG BASE_IMAGE=localhost/apme-base:latest
FROM $BASE_IMAGE

ENV APME_ENGINE_ADDRESS=127.0.0.1:50051
WORKDIR /workspace

USER 1001

ENTRYPOINT ["apme"]
CMD ["check", "."]
