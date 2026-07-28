#!/usr/bin/env python3
"""Build a deployment-local Codex-through-Omnigent promotion document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
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
    evidence_dir = args.output.parent / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    staged_artifacts = []
    for index, artifact in enumerate(args.artifact):
        staged = evidence_dir / f"{index:02d}-{artifact.name}"
        shutil.copy2(artifact, staged)
        staged_artifacts.append(staged)
    document = build_cutover_evidence(
        release=release,
        artifact_paths=staged_artifacts,
    )
    for item in document["evidenceManifest"]:
        item["ref"] = f"evidence/{Path(item['ref']).name}"
    document["evidenceRefs"] = [
        item["ref"] for item in document["evidenceManifest"]
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
