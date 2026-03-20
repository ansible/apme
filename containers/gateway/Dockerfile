# Web Gateway: FastAPI container outside the engine pod.
# Translates HTTP/WebSocket to gRPC; owns SQLite persistence.
# Connects to Primary at APME_PRIMARY_ADDRESS over the network.
FROM python:3.12-slim

WORKDIR /app

# Copy full source tree — setuptools needs the src/ layout and pyproject.toml
COPY pyproject.toml .
COPY src/ src/

# Copy rule catalog for the rules endpoint
COPY docs/RULE_CATALOG.md docs/RULE_CATALOG.md

# Install the base package (grpcio, protobuf) + gateway extras (fastapi, etc.)
RUN pip install --no-cache-dir ".[gateway]"

# Persistence volume
RUN mkdir -p /data
VOLUME /data

ENV APME_PRIMARY_ADDRESS=primary:50051
ENV APME_GATEWAY_HOST=0.0.0.0
ENV APME_GATEWAY_PORT=8080
ENV APME_DATABASE_URL=sqlite+aiosqlite:////data/apme.db

EXPOSE 8080

CMD ["python", "-m", "apme_gateway"]
