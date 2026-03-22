# Gateway: gRPC Reporting receiver + FastAPI REST API.
# Persists scan/fix events to SQLite (ADR-020 / ADR-029).
# Mount /data for SQLite persistence across restarts.
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[gateway]"

ENV APME_DB_PATH=/data/apme.db
ENV APME_GATEWAY_GRPC_LISTEN=0.0.0.0:50060
ENV APME_GATEWAY_HTTP_HOST=0.0.0.0
ENV APME_GATEWAY_HTTP_PORT=8080
EXPOSE 50060 8080

VOLUME /data

CMD ["apme-gateway"]
