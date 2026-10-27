#!/usr/bin/env bash
set -euo pipefail

# Separate CI/host entrypoint. Managed workflows use container python-tests;
# they never receive this socket or run nested Docker.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${MOONMIND_PYTHON_TEST_IMAGE:-moonmind-python-tests:ci}"
mkdir -p "$root/artifacts/integration-ci/scoped-egress"
docker run --rm --user root \
  --name "moonmind-test-egress-driver-$$" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --mount "type=bind,src=$root,dst=$root" \
  --workdir "$root" \
  --env "PYTHONPATH=$root" \
  --env MOONMIND_RUN_SCOPED_EGRESS_DOCKER=1 \
  --env "MOONMIND_SCOPED_TEST_IMAGE=$image" \
  --env "MOONMIND_TEST_CANDIDATE=$(git -C "$root" rev-parse HEAD)" \
  --entrypoint python "$image" -m pytest \
  tests/integration/security/test_scoped_egress_docker.py \
  -q --tb=short --timeout=180 \
  --junitxml=artifacts/integration-ci/pytest-scoped-egress.xml
