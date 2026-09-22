# Standalone MoonMind deployment controller image (issue #4500).
# Stdlib-only: no MoonMind application modules, API, DB, Temporal, artifact
# service, provider manager, Omnigent, or LLM is required at runtime.
FROM python:3.12-slim

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
