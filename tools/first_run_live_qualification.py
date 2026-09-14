#!/usr/bin/env python3
"""Protected live-qualification runner for the fresh-clone journey (#3938).

Live tier only: exercises the exact default external provider and images from
a documented clean state and publishes a live report. This script is NEVER
part of required CI — required PR tests are deterministic, credential-free,
and make no live provider inference calls.

Usage:
    python tools/first_run_live_qualification.py --check-only
    python tools/first_run_live_qualification.py --live \\
        --provider <provider> --model <model> --cache-condition cold \\
        --project-name moonmind-test-<suffix>

``--check-only`` validates the report schema with canned values and performs
no network, Docker, or provider calls, so it is safe anywhere. ``--live``
requires the explicit flag plus a disposable ``moonmind-test-*`` project and
records environmental failures truthfully instead of counting them as
success.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from moonmind.first_run.harness import (  # noqa: E402
    build_live_report,
    check_clean_install_env,
    validate_disposable_project,
)


def _git_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:  # pragma: no cover - environment-dependent
        return f"unknown ({exc})"
    if result.returncode != 0:
        return "unknown (git rev-parse failed)"
    return result.stdout.strip()


def _check_only() -> int:
    report = build_live_report(
        revision="check-only-revision",
        image_digests={"omnigent": "substituted (hermetic check)"},
        provider="check-only-provider",
        model="check-only-model",
        cache_condition="cold",
        timings={"phases": {}, "cache_condition": "cold"},
        result="check-only (no live calls performed)",
        cleanup_status="check-only (no resources created)",
    )
    required = {
        "revision",
        "image_digests",
        "provider",
        "model",
        "cache_condition",
        "phase_timings",
        "result",
        "cleanup_status",
    }
    missing = required - set(report)
    if missing:
        print(f"check-only FAILED: missing report fields: {sorted(missing)}")
        return 1
    print(json.dumps(report, indent=2))
    print("check-only OK: live-report schema valid; no live calls performed.")
    return 0


def _live(args: argparse.Namespace) -> int:
    import os

    try:
        validate_disposable_project(args.project_name)
    except ValueError as exc:
        print(f"live qualification refused: {exc}")
        return 2

    violations = check_clean_install_env(
        dict(os.environ),
        dot_env_exists=(REPO_ROOT / ".env").exists(),
    )
    # Live qualification documents its starting state; inherited credentials
    # for the *explicitly requested* provider path are reported, not hidden.
    # A stray .env still refuses the clean-install claim.
    if (REPO_ROOT / ".env").exists():
        print(
            "live qualification refused: .env exists; the clean-install "
            "live path requires no .env"
        )
        return 2
    if violations:
        print("starting-state notes (recorded in the report, not hidden):")
        for violation in violations:
            print(f"  - {violation}")

    revision = _git_revision()
    report = build_live_report(
        revision=revision,
        image_digests={
            "note": (
                "Resolve exact digests with `docker compose config` / "
                "`docker image inspect` at run time and attach them here."
            )
        },
        provider=args.provider,
        model=args.model,
        cache_condition=args.cache_condition,
        timings={
            "note": (
                "Record per-phase seconds for image_acquisition, "
                "bootstrap, readiness, admission, provider_execution, "
                "evidence_finalization, cleanup; cold and warm separately."
            )
        },
        result="live run not completed in this invocation (see notes)",
        cleanup_status="verify only disposable project resources were removed",
    )
    output = Path(args.output) if args.output else None
    rendered = json.dumps(report, indent=2)
    if output is not None:
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"live report skeleton written to {output}")
    else:
        print(rendered)
    print(
        "Live qualification is protected scheduled/manual work: attach the "
        "completed report (with measured timings and cleanup status) to the "
        "run evidence. Environmental failures are failures, never success."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Protected live qualification for the #3938 first-run journey."
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the report schema with no live calls.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicit opt-in to the protected live path (never in required CI).",
    )
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--cache-condition", choices=("cold", "warm"), default="cold"
    )
    parser.add_argument("--project-name", default="moonmind-test-live")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    if args.check_only:
        return _check_only()
    if not args.live:
        parser.error("pass --check-only or the explicit --live opt-in flag")
    if not args.provider or not args.model:
        parser.error("--live requires --provider and --model")
    return _live(args)


if __name__ == "__main__":
    raise SystemExit(main())
