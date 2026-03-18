# Gitleaks validator: gitleaks binary + Python gRPC shim.
# Multi-stage: copies gitleaks from the official image into a Python image
# that runs our gRPC wrapper.
FROM docker.io/zricethezav/gitleaks:latest AS gitleaks-bin

FROM python:3.12-slim

COPY --from=gitleaks-bin /usr/bin/gitleaks /usr/local/bin/gitleaks

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

ENV APME_GITLEAKS_VALIDATOR_LISTEN=0.0.0.0:50056
EXPOSE 50056

CMD ["apme-gitleaks-validator"]
