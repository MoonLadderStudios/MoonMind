# Standalone MoonMind deployment controller image (issue #4500).
# Stdlib-only: no MoonMind application modules, API, DB, Temporal, artifact
# service, provider manager, Omnigent, or LLM is required at runtime.
FROM python:3.12-slim

# The controller invokes `docker ps` and `docker compose` against the mounted
# Docker socket; the socket alone does not provide the executables. The
# Compose v2 plugin is published only in Docker's apt repository (Debian
# ships no `docker-compose-plugin`), the same source the app image uses.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MOONMIND_CONTROLLER_MANAGED=1 \
    MOONMIND_CONTROLLER_STATE_DIR=/var/lib/moonmind-controller \
    MOONMIND_CONTROLLER_PORT=8472

WORKDIR /opt/moonmind-controller
COPY __init__.py redact.py mounts.py lock.py record.py engine.py server.py ./

VOLUME ["/var/lib/moonmind-controller"]
EXPOSE 8472

ENTRYPOINT ["python", "server.py"]
