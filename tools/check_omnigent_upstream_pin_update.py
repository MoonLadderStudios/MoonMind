#!/usr/bin/env python3
"""Evaluate one omnigent upstream-pin updater check (hermetic planner).

Source issue: MoonLadderStudios/MoonMind#3957.

Reads the current ``omnigent`` gitlink commit and an already-fetched upstream
release inventory (GitHub releases API payload where each entry carries a
``resolved_commit`` immutable SHA resolved from its tag), then writes three
artifacts into the output directory:

* ``candidate.json``   — explicit planner outcome (closed vocabulary)
* ``evidence.json``    — exact-artifact evidence record (old/new commit,
  release provenance, affected assets, pinned qualification inputs,
  rollback identity)
* ``status.json``      — freshness publication (available / tested /
  qualified / promoted stages, blocked reason, last qualified version)

The tool never mutates the gitlink, never executes fetched upstream code,
and never consumes production secrets.  All explicit domain outcomes exit 0;
only tool misuse or unreadable inputs exit 2.  A transient upstream fetch
failure is reported with ``--fetch-error`` instead of a releases file.

Usage:
    python tools/check_omnigent_upstream_pin_update.py \\
        --current-commit f04b0354fb5344c1ea8b92795ceb6760a9ad7595 \\
        --releases-json /tmp/upstream-releases.json \\
        --output-dir artifacts/omnigent-upstream-pin-update
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.upstream_pin_update import (  # noqa: E402
    UPSTREAM_REPO_DEFAULT,
    UpstreamPinUpdateError,
    blocked_transient_upstream,
    build_freshness_status,
    build_update_evidence,
    select_candidate,
)


def _load_releases(path: str) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("releases"), list):
        return raw["releases"]
    if isinstance(raw, list):
        return raw
    raise UpstreamPinUpdateError(
        "releases JSON must be an array or an object with a 'releases' array"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-commit", required=True)
    parser.add_argument("--releases-json", default="")
    parser.add_argument("--fetch-error", default="")
    parser.add_argument("--allow-prereleases", action="store_true")
    parser.add_argument("--upstream-repo", default=UPSTREAM_REPO_DEFAULT)
    parser.add_argument("--api-host-ui-changes", default="")
    parser.add_argument("--last-qualified-commit", default="")
    parser.add_argument("--last-qualified-tag", default="")
    parser.add_argument("--promoted-commit", default="")
    parser.add_argument("--blocked-reason", default="")
    parser.add_argument("--checked-at", default="")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    try:
        if args.fetch_error:
            result = blocked_transient_upstream(args.fetch_error)
        else:
            if not args.releases_json:
                raise UpstreamPinUpdateError(
                    "either --releases-json or --fetch-error is required"
                )
            releases = _load_releases(args.releases_json)
            result = select_candidate(
                args.current_commit,
                releases,
                allow_prereleases=args.allow_prereleases,
                upstream_repo=args.upstream_repo,
            )
        checked_at = args.checked_at or None
        evidence = build_update_evidence(
            result=result,
            current_commit=args.current_commit,
            upstream_repo=args.upstream_repo,
            api_host_ui_changes=args.api_host_ui_changes,
            checked_at=checked_at,
        )
        status = build_freshness_status(
            result=result,
            current_commit=args.current_commit,
            last_qualified_commit=args.last_qualified_commit or None,
            last_qualified_tag=args.last_qualified_tag,
            promoted_commit=args.promoted_commit or None,
            blocked_reason=args.blocked_reason,
            checked_at=checked_at,
        )
    except (UpstreamPinUpdateError, json.JSONDecodeError, OSError) as exc:
        print(f"::error::omnigent upstream-pin check failed: {exc}")
        return 2

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_doc = {
        "schemaVersion": "moonmind.omnigent-upstream-pin-candidate/v1",
        "status": result.status,
        "reason": result.reason,
        "candidate": result.candidate,
        "allowPrereleases": args.allow_prereleases,
        "upstreamRepo": args.upstream_repo,
    }
    (out_dir / "candidate.json").write_text(
        json.dumps(candidate_doc, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "evidence.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "status.json").write_text(
        json.dumps(status, indent=2) + "\n", encoding="utf-8"
    )
    level = "notice" if result.status in ("up_to_date", "candidate_available") else "warning"
    print(f"::{level}::omnigent upstream-pin outcome: {result.status} — {result.reason}")
    print(f"candidate={json.dumps(result.candidate or {})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
