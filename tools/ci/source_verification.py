"""Same-run source evidence handoff between release source and artifact phases."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SCHEMA = "moonmind.source-verification/v1"
SOURCE_OWNERS = (
    "unit_fast",
    "unit_slow",
    "api_component",
    "temporal_boundary",
    "integration_hermetic",
    "reliability_journey",
    "omnigent_conformance",
    "frontend_static",
    "frontend_browser_chromium",
    "frontend_browser_firefox",
    "generated_contracts",
)


def artifact_attempt(
    name: str, *, prefix: str, run_id: str, current_attempt: str
) -> str:
    """Admit a retained successful job only within this immutable Actions run."""
    match = re.fullmatch(
        re.escape(prefix + "-" + run_id + "-") + r"([1-9][0-9]*)", name
    )
    if (
        not match
        or not current_attempt.isdecimal()
        or int(match[1]) > int(current_attempt)
    ):
        raise ValueError("Artifact does not belong to an admitted attempt of this run")
    return match[1]


def source_receipt(
    *, revision: str, run_id: str, attempt: str, selection: dict, result: str
) -> dict:
    if (
        not re.fullmatch(r"[0-9a-f]{40}", revision)
        or not run_id.isdecimal()
        or not attempt.isdecimal()
    ):
        raise ValueError("Missing immutable source/run identity")
    if result != "success" or any(
        selection.get(key) != "true" for key in SOURCE_OWNERS
    ):
        raise ValueError("Complete source verification did not succeed")
    return {
        "schema": SCHEMA,
        "revision": revision,
        "runId": run_id,
        "attempt": attempt,
        "status": "passed",
        "owners": list(SOURCE_OWNERS),
    }


def validate_source_receipt(
    payload: dict, *, revision: str, run_id: str, attempt: str
) -> None:
    expected = source_receipt(
        revision=revision,
        run_id=run_id,
        attempt=attempt,
        selection=dict.fromkeys(SOURCE_OWNERS, "true"),
        result="success",
    )
    if payload != expected:
        raise ValueError(
            "Source verification receipt is incomplete or belongs to another source/run/attempt"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("write", "verify"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--selection", default="{}")
    parser.add_argument("--result", default="")
    parser.add_argument("--artifact")
    args = parser.parse_args()
    identity = dict(revision=args.revision, run_id=args.run_id, attempt=args.attempt)
    if args.artifact:
        identity["attempt"] = artifact_attempt(
            args.artifact,
            prefix="source-verification",
            run_id=args.run_id,
            current_attempt=args.attempt,
        )
    if args.action == "write":
        receipt = source_receipt(
            **identity, selection=json.loads(args.selection), result=args.result
        )
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(json.dumps(receipt, indent=2) + "\n")
    else:
        validate_source_receipt(json.loads(args.path.read_text()), **identity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
