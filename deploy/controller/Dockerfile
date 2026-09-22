# Standalone MoonMind deployment controller image (issue #4500).
# Stdlib-only: no MoonMind application modules, API, DB, Temporal, artifact
# service, provider manager, Omnigent, or LLM is required at runtime.
FROM python:3.12-slim

# The controller invokes `docker ps` and `docker compose` against the mounted
# Docker socket; the socket alone does not provide the executables.
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io docker-compose-plugin \
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
