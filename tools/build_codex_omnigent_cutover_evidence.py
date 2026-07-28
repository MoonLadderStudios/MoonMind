#!/usr/bin/env python3
"""Build a deployment-local Codex-through-Omnigent promotion document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.cutover_conformance import build_cutover_evidence  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--artifact", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    release = json.loads(args.release.read_text(encoding="utf-8"))
    if not isinstance(release, dict):
        raise ValueError("release configuration must be an object")
    document = build_cutover_evidence(
        release=release,
        artifact_paths=args.artifact,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
