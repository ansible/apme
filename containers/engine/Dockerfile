# Engine daemon: gRPC orchestrator for scan/fix pipelines.
ARG BASE_IMAGE=localhost/apme-base:latest
FROM $BASE_IMAGE

ENV APME_ENGINE_LISTEN=0.0.0.0:50051
ENV APME_COLLECTION_CACHE=/sessions
EXPOSE 50051

USER 1001

CMD ["apme-engine"]
