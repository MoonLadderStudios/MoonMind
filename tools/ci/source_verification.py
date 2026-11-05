"""Same-run source evidence handoff between release source and artifact phases."""

from __future__ import annotations

import argparse
import hashlib
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


def content_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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
        "selectionDigest": content_digest(
            json.dumps(selection, sort_keys=True, separators=(",", ":")).encode()
        ),
    }


def validate_source_receipt(
    payload: dict, *, revision: str, run_id: str, attempt: str, selection: dict
) -> None:
    """Admit only this run's complete receipt for the selection it admitted."""
    expected = source_receipt(
        revision=revision,
        run_id=run_id,
        attempt=attempt,
        selection=selection,
        result="success",
    )
    if payload != expected:
        raise ValueError(
            "Source verification receipt is incomplete or belongs to another "
            "source/run/attempt/selection"
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
    parser.add_argument("--digest", default="", help="Expected receipt sha256")
    args = parser.parse_args()
    try:
        identity = dict(
            revision=args.revision, run_id=args.run_id, attempt=args.attempt
        )
        if args.artifact:
            identity["attempt"] = artifact_attempt(
                args.artifact,
                prefix="source-verification",
                run_id=args.run_id,
                current_attempt=args.attempt,
            )
        selection = json.loads(args.selection)
        if not isinstance(selection, dict):
            raise ValueError("Admitted selection is not an object")
        if args.action == "write":
            receipt = source_receipt(
                **identity, selection=selection, result=args.result
            )
            args.path.parent.mkdir(parents=True, exist_ok=True)
            args.path.write_text(json.dumps(receipt, indent=2) + "\n")
            return 0
        data = args.path.read_bytes()
        if not args.digest or content_digest(data) != args.digest:
            raise ValueError("Source receipt bytes do not match the handed-off digest")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Source receipt is not an object")
        validate_source_receipt(payload, **identity, selection=selection)
    except (OSError, ValueError) as error:
        print(f"::error::Source verification {args.action} refused: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
