#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from moonmind.workflows.temporal.type_safety_gates import build_self_check_findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate deterministic Temporal type-safety review gates."
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Evaluate representative safe and unsafe fixtures from MM-331.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    if not args.self_check:
        parser.error("--self-check is required until repository scanning fixtures are added")

    findings = build_self_check_findings()
    payload = {"findings": [finding.model_dump(by_alias=True) for finding in findings]}

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for finding in findings:
            print(
                f"{finding.status.upper()} {finding.rule_id} {finding.target}: "
                f"{finding.message}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
