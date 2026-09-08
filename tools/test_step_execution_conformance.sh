#!/usr/bin/env bash
set -euo pipefail

python -m moonmind.workflows.temporal.step_execution_conformance "$@"
