# Ansible validator: async gRPC server, read-only consumer of session venvs.
# UV cache is pre-warmed at build time so venv creation by Engine is near-instant.
# Sessions volume mounted read-only from the Engine.
ARG BASE_IMAGE=localhost/apme-base:latest
FROM $BASE_IMAGE

USER root

# Pre-warm UV cache for supported ansible-core versions
ENV UV_CACHE_DIR=/opt/uv-cache
RUN mkdir -p /opt/uv-cache \
    && chmod +x containers/ansible/prebuild-venvs.sh \
    && containers/ansible/prebuild-venvs.sh \
    && chown -R 1001:0 /opt/uv-cache

USER 1001

ENV APME_ANSIBLE_VALIDATOR_LISTEN=0.0.0.0:50053
ENV APME_COLLECTION_CACHE=/sessions
EXPOSE 50053

CMD ["apme-ansible-validator"]
