#!/usr/bin/env bash
set -euo pipefail

docker compose -f docker-compose.test.yaml build pytest
docker compose -f docker-compose.test.yaml run --rm pytest \
  bash -lc "pytest tests/integration -m 'integration_ci' -q --tb=short"
