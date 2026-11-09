#!/usr/bin/env bash
set -euo pipefail

# Separate CI/host entrypoint. Managed workflows use container python-tests;
# they never receive this socket or run nested Docker.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${MOONMIND_PYTHON_TEST_IMAGE:-moonmind-python-tests:ci}"
mkdir -p "$root/artifacts/integration-ci/scoped-egress"
# The dependency-only CI image lacks production source. This disposable overlay
# copies only the exact stdlib gateway under test; production uses its installed
# runtime image without bind-mounted or injected source.
native_image="moonmind-test-native-egress:$$"
inventory_image="moonmind-test-service-inventory:$$"
overlay="$(mktemp -d "$root/artifacts/integration-ci/scoped-egress/overlay.XXXXXX")"
cleanup_native_image() {
  docker image rm "$native_image" "$inventory_image" >/dev/null 2>&1 || true
  rm -rf "$overlay"
}
trap cleanup_native_image EXIT
printf 'FROM %s\nCOPY moonmind/security/native_tcp_gateway.py /app/moonmind/security/native_tcp_gateway.py\nUSER root\nRUN install -d -o proxy -g proxy -m 0700 /var/lib/moonmind-native-egress\nUSER app\n' "$image" > "$overlay/Dockerfile"
docker build --network none -f "$overlay/Dockerfile" -t "$native_image" "$root"
# Exercise the production runner staging and isolated installed import path.
# Dependencies come from the existing test image, not a claimed release image.
python3 - "$root" "$overlay/python" <<'PY_STAGE'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tools.ci.repository_security_image import stage_runner
stage_runner(Path(sys.argv[2]))
PY_STAGE
cat > "$overlay/Inventory.Dockerfile" <<EOF
FROM $image
USER root
COPY python/ /opt/moonmind-scanner-python/
RUN python -c "import sysconfig; from pathlib import Path; Path(sysconfig.get_paths()['purelib'], 'moonmind-scanner.pth').write_text('/opt/moonmind-scanner-python\\n')"
USER app
RUN python -I -c "from moonmind.security import service_inventory_runner as r; assert r.__file__.startswith('/opt/moonmind-scanner-python/'), r.__file__"
EOF
docker build --network none -f "$overlay/Inventory.Dockerfile" -t "$inventory_image" "$overlay"
docker run --rm --user root \
  --name "moonmind-test-egress-driver-$$" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --mount "type=bind,src=$root,dst=$root" \
  --workdir "$root" \
  --env "PYTHONPATH=$root" \
  --env MOONMIND_RUN_SCOPED_EGRESS_DOCKER=1 \
  --env "MOONMIND_SCOPED_TEST_IMAGE=$image" \
  --env "MOONMIND_NATIVE_TEST_IMAGE=$native_image" \
  --env "MOONMIND_INVENTORY_TEST_IMAGE=$inventory_image" \
  --env "MOONMIND_TEST_CANDIDATE=$(git -C "$root" rev-parse HEAD)" \
  --entrypoint python "$image" -m pytest \
  tests/integration/security/test_realtime_scoped_egress.py \
  tests/integration/security/test_scoped_egress_docker.py \
  tests/integration/security/test_native_scoped_egress_docker.py \
  tests/integration/security/test_service_inventory_admission.py \
  tests/integration/security/test_service_inventory_docker.py \
  -q --tb=short --timeout=180 \
  --junitxml=artifacts/integration-ci/pytest-scoped-egress.xml
