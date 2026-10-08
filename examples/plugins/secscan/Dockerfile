# ansible-security-scanner Plugin sidecar (ADR-042).
# Wraps https://github.com/cpeoples/ansible-security-scanner (Apache-2.0).
# Build from repo root:
#   podman build -t apme-plugin-secscan:latest -f examples/plugins/secscan/Dockerfile .
ARG BASE_IMAGE=localhost/apme-base:latest
ARG SECSCAN_VERSION=0.1.39
FROM $BASE_IMAGE

USER root

ARG SECSCAN_VERSION
RUN uv pip install --python /app/.venv/bin/python \
    "ansible-security-scanner==${SECSCAN_VERSION}"

COPY examples/plugins/secscan/plugin.py /app/plugin.py
RUN chmod a+r /app/plugin.py

USER 1001

ENV APME_PLUGIN_LISTEN=0.0.0.0:50101
EXPOSE 50101

ENTRYPOINT ["python", "/app/plugin.py"]
