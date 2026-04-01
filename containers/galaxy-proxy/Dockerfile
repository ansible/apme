# Galaxy proxy: PEP 503 bridge from Ansible Galaxy to pip/uv.
# Serves collections as Python wheels on demand; caches internally.
# Mount cache volume at /cache for persistence across restarts.
# Requires ansible-core for `ansible-galaxy collection download` (ADR-045).
ARG BASE_IMAGE=localhost/apme-base:latest
FROM $BASE_IMAGE

# ansible-galaxy collection download is used for tarball fetching (ADR-045).
# Install ansible-core into the image venv so the binary is on PATH.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python "ansible-core>=2.16,<2.19"

EXPOSE 8765

VOLUME /cache

ENTRYPOINT ["galaxy-proxy"]
CMD ["--host", "0.0.0.0", "--port", "8765", "--cache-dir", "/cache"]
