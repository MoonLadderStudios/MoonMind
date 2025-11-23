#!/usr/bin/env bash
set -euo pipefail

# Run only unit tests
python -m pytest -q tests/unit

