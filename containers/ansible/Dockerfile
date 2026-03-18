# Ansible validator: async gRPC server with ephemeral per-request venvs.
# UV cache is pre-warmed at build time so runtime venv creation is near-instant.
# Mount collection cache volume at /cache (read-only).
FROM python:3.12-slim

RUN pip install --no-cache-dir uv ansible-builder

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# Pre-warm UV cache for supported ansible-core versions
ENV UV_CACHE_DIR=/opt/uv-cache
RUN chmod +x containers/ansible/prebuild-venvs.sh \
    && containers/ansible/prebuild-venvs.sh

ENV APME_ANSIBLE_VALIDATOR_LISTEN=0.0.0.0:50053
ENV APME_CACHE_ROOT=/cache
EXPOSE 50053

CMD ["apme-ansible-validator"]
