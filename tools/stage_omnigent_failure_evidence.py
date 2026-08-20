#!/usr/bin/env python3
"""Stage bounded, redacted failure evidence for a live conformance case.

Source issue: MoonLadderStudios/MoonMind#3710.

When a credentialed live conformance case fails before the final
secret-safety gate, the pipeline must still upload a *safe* failure summary,
the setup stage reached, runner health, sanitized log lines, and the case
duration — not a single opaque ``withheld`` marker.  This tool writes that
bounded, redacted ``failure-diagnostics.json`` document into the upload
directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.live_verification_health import (  # noqa: E402
    DEFAULT_MAX_LOG_LINES,
    LiveVerificationHealthError,
    build_safe_failure_diagnostics,
)


def _read_log_tail(log_file: str | None, max_lines: int) -> list[str]:
    if not log_file:
        return []
    path = Path(log_file)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max_lines:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--setup-stage", required=True)
    parser.add_argument("--failure-summary", required=True)
    parser.add_argument("--runner-status", default="")
    parser.add_argument("--duration-seconds", type=float, default=None)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--max-log-lines", type=int, default=DEFAULT_MAX_LOG_LINES)
    parser.add_argument("--upload-dir", required=True)
    args = parser.parse_args(argv)

    upload_dir = Path(args.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    runner_health = {"status": args.runner_status} if args.runner_status else {}
    try:
        diagnostics = build_safe_failure_diagnostics(
            mode=args.mode,
            outcome=args.outcome,
            setup_stage=args.setup_stage,
            runner_health=runner_health,
            failure_summary=args.failure_summary,
            log_tail=_read_log_tail(args.log_file, args.max_log_lines),
            duration_seconds=args.duration_seconds,
            max_log_lines=args.max_log_lines,
        )
    except LiveVerificationHealthError as exc:
        print(f"::error::could not stage safe failure evidence: {exc}")
        return 2

    output_path = upload_dir / "failure-diagnostics.json"
    output_path.write_text(json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8")
    print(f"wrote bounded redacted failure diagnostics to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
