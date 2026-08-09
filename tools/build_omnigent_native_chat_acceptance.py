#!/usr/bin/env python3
"""Build the fail-closed MoonLadderStudios/MoonMind#3642 report."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moonmind.omnigent.native_chat_acceptance import build_native_chat_acceptance_report

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    report = build_native_chat_acceptance_report(
        json.loads(args.source.read_text(encoding="utf-8")),
        evidence_root=args.evidence_root, expected_commit=args.expected_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
