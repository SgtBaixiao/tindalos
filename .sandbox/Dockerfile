# syntax=docker/dockerfile:1
# Tindalos hardened code-execution image (superset of coc-sandbox-exec).
# Rootfs read-only at runtime; /workspace is the only rw mount; no network.
FROM python:3.12-slim

RUN useradd -l -M -u 10001 -s /bin/sh appuser \
 && mkdir -p /workspace /tmp \
 && chown appuser:appuser /workspace \
 && chmod 1777 /tmp

COPY .sandbox/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

USER appuser
WORKDIR /workspace
ENV HOME=/tmp \
    PATH="/usr/local/bin:/usr/bin:/bin" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
