#!/usr/bin/env python3
"""Build the fail-closed MoonLadderStudios/MoonMind#3642 native Chat gate report.

Validates the combined deterministic + protected-live acceptance evidence and
writes the single versioned report only when every required scenario row passes.
Mirrors ``tools/build_omnigent_embedded_acceptance.py`` so the two publication
gates are operated the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.native_chat_acceptance import (
    build_native_chat_acceptance_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    source = json.loads(args.evidence.read_text(encoding="utf-8"))
    resolver = None
    if args.evidence_root is not None:
        root = args.evidence_root.resolve()

        def resolver(ref: str):
            if not ref.startswith("artifact://"):
                raise ValueError("evidence refs must use artifact://")
            path = (root / (ref.removeprefix("artifact://") + ".json")).resolve()
            if root not in path.parents:
                raise ValueError("evidence ref escapes evidence root")
            return json.loads(path.read_text(encoding="utf-8"))

    report = build_native_chat_acceptance_report(
        source, expected_commit=args.expected_commit, evidence_resolver=resolver
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
