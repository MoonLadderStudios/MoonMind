#!/usr/bin/env python3
"""Build and validate protected Workflow Chat rollout evidence for #3632/#3642."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.workflow_chat_acceptance import (  # noqa: E402
    build_workflow_chat_acceptance_manifest,
    validate_workflow_chat_acceptance_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bind a protected browser-to-stock-host Workflow Chat matrix to "
            "independently resolvable, secret-scanned evidence."
        )
    )
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()

    matrix_path = args.matrix.resolve()
    source = json.loads(matrix_path.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise ValueError("workflow Chat matrix must be an object")
    evidence_root = matrix_path.parent
    output_path = args.output.resolve()
    if output_path.parent != evidence_root:
        raise ValueError(
            "workflow Chat acceptance output must stay beside its evidence matrix"
        )
    manifest = build_workflow_chat_acceptance_manifest(
        source,
        evidence_root=evidence_root,
    )
    validate_workflow_chat_acceptance_manifest(
        manifest,
        evidence_root=evidence_root,
        expected_commit=args.expected_commit,
    )
    output_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
