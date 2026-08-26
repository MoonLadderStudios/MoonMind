#!/usr/bin/env python3
"""Build the #3368 aggregate report from bounded runner evidence.

Runner input is JSON with ``images``, host metadata, and one result per case.
The command intentionally refuses mutable image tags, incomplete coverage, and
secret-like evidence before publishing the report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import CaseResult, build_report, load_profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("tests/fixtures/omnigent/conformance-v4.json"),
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="allow skipped critical cases for explicitly partial CI reports",
    )
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    profile = load_profile(args.profile)
    cases = [
        CaseResult(
            case_id=item["caseId"],
            status=item["status"],
            evidence_refs=tuple(item.get("evidenceRefs", ())),
            diagnostics=tuple(item.get("diagnostics", ())),
        )
        for item in evidence["cases"]
    ]
    report = build_report(
        profile=profile,
        images=evidence["images"],
        host_architecture=evidence["hostArchitecture"],
        auth_mode=evidence["authMode"],
        protocol_version=evidence["protocolVersion"],
        capabilities=evidence.get("capabilities", ()),
        evidence_scans=evidence["evidenceScans"],
        cases=cases,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    critical_ids = {case["id"] for case in profile["cases"] if case["critical"]}
    skipped_critical = {
        case["caseId"]
        for case in report["cases"]
        if case["status"] == "skipped" and case["caseId"] in critical_ids
    }
    return 1 if report["summary"]["failed"] or (
        skipped_critical and not args.allow_partial
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
