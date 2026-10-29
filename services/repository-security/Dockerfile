# Verified upstream binaries and their inventory are staged by the CI resolver.
ARG SECURITY_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${SECURITY_BASE_IMAGE}
ARG SECURITY_BASE_IDENTITY=unknown

RUN apt-get -o Acquire::Retries=3 update \
    && apt-get install --no-install-recommends -y git ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir 'pydantic>=2.12.5,<2.15.0' \
    && useradd --uid 1000 --create-home --home-dir /home/scanner scanner \
    && mkdir -p /workspace /scan-output /opt/moonmind-scanner-db /opt/moonmind-scanner-rules \
    && chown 1000:1000 /scan-output

COPY services/repository-security/resolved/bin/ /usr/local/bin/
COPY services/repository-security/resolved/python/ /opt/moonmind-scanner-python/
COPY services/repository-security/resolved/notices/ /usr/local/share/repository-security/notices/
COPY services/repository-security/resolved/inventory.json /usr/local/share/repository-security/inventory.json
COPY LICENSE NOTICE /usr/local/share/repository-security/notices/
RUN SECURITY_BASE_IDENTITY="${SECURITY_BASE_IDENTITY}" python - <<'PY'
import importlib.metadata
import json
import os
import subprocess
import sysconfig
from pathlib import Path
Path(sysconfig.get_paths()['purelib'], 'moonmind-scanner.pth').write_text('/opt/moonmind-scanner-python\n')
path = Path('/usr/local/share/repository-security/inventory.json')
inventory = json.loads(path.read_text())
inventory['baseIdentity'] = os.environ['SECURITY_BASE_IDENTITY']
inventory['pythonPackages'] = {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}
inventory['systemPackages'] = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}=${Version}\n'], text=True).splitlines()
path.write_text(json.dumps(inventory, indent=2) + '\n')
PY

LABEL org.opencontainers.image.title="MoonMind repository security tools" \
      org.opencontainers.image.source="https://github.com/MoonLadderStudios/MoonMind"
ENV HOME=/tmp TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
WORKDIR /workspace
# Tools and the admitted Python runner are independently invocable.
CMD ["python", "-c", "from pathlib import Path; print(Path('/usr/local/share/repository-security/inventory.json').read_text())"]
