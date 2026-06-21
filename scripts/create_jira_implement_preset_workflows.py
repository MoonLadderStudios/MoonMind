#!/usr/bin/env python3
"""Compatibility wrapper for Jira Implement batch workflow creation."""

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.create_issue_implement_preset_workflows import (
    DEFAULT_PRESET_VERSION as _GENERIC_DEFAULT_PRESET_VERSION,
    DEFAULT_REPOSITORY,
    DEFAULT_RUNTIME,
    build_expand_payload as _build_expand_payload,
    build_idempotency_key as _build_idempotency_key,
    build_payload as _build_payload,
    expand_issue_implement,
    main,
    post_json,
)

DEFAULT_PRESET_VERSION = "1.1.0"

def build_idempotency_key(*, issue_key: str, repository: str, runtime: str, preset_version: str = DEFAULT_PRESET_VERSION) -> str:
    return _build_idempotency_key(provider="jira", issue_ref=issue_key.strip().upper(), repository=repository, runtime=runtime, preset_version=preset_version)

def build_payload(*, issue_key: str, repository: str, runtime: str, expanded_steps: list[dict], applied_template: dict | None = None, preset_version: str = DEFAULT_PRESET_VERSION) -> dict:
    return _build_payload(provider="jira", issue_ref=issue_key.strip().upper(), repository=repository, runtime=runtime, expanded_steps=expanded_steps, applied_template=applied_template, preset_version=preset_version)

def build_expand_payload(*, issue_key: str, repository: str, runtime: str, preset_version: str = DEFAULT_PRESET_VERSION) -> dict:
    return _build_expand_payload(provider="jira", issue=issue_key, repository=repository, runtime=runtime, preset_version=preset_version)

def expand_jira_implement(*, base_url: str, issue_key: str, repository: str, runtime: str, preset_version: str, timeout: float) -> dict:
    return expand_issue_implement(base_url=base_url, provider="jira", issue=issue_key, repository=repository, runtime=runtime, preset_version=preset_version, timeout=timeout)

# Override imported generic main so legacy tests and callers can monkeypatch this module.
def parse_args():
    import argparse, os
    parser = argparse.ArgumentParser(description="Create Jira Implement preset workflows with publish mode PR and merge automation enabled.")
    parser.add_argument("issues", nargs="+", help="Jira issue keys, e.g. MM-770")
    parser.add_argument("--base-url", default=os.environ.get("MOONMIND_URL", "http://api:8000"))
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--runtime", default=DEFAULT_RUNTIME)
    parser.add_argument("--preset-version", default=DEFAULT_PRESET_VERSION)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    import json, time
    args = parse_args()
    results: list[dict] = []
    failures: list[dict] = []
    for issue in args.issues:
        issue_key = issue.strip().upper()
        if args.dry_run:
            results.append({"issue": issue_key, "expandPayload": build_expand_payload(issue_key=issue_key, repository=args.repository, runtime=args.runtime, preset_version=args.preset_version)})
            continue
        try:
            expanded = expand_jira_implement(base_url=args.base_url, issue_key=issue_key, repository=args.repository, runtime=args.runtime, preset_version=args.preset_version, timeout=args.timeout)
        except Exception as exc:
            record = {"issue": issue_key, "status": 0, "error": f"Expansion failed: {exc}"}
            failures.append(record); results.append(record); continue
        raw_steps = expanded.get("steps")
        if not isinstance(raw_steps, list) or len(raw_steps) != 8:
            record = {"issue": issue_key, "error": f"Expected jira-implement expansion to produce 8 steps; got {len(raw_steps) if isinstance(raw_steps, list) else 0}."}
            failures.append(record); results.append(record); continue
        applied_template = expanded.get("appliedTemplate") if isinstance(expanded.get("appliedTemplate"), dict) else None
        payload = build_payload(issue_key=issue_key, repository=args.repository, runtime=args.runtime, expanded_steps=raw_steps, applied_template=applied_template, preset_version=args.preset_version)
        response = post_json(base_url=args.base_url, payload=payload, timeout=args.timeout)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        record = {"issue": issue_key, "status": response.get("status"), "workflowId": body.get("workflowId") or body.get("id"), "runId": body.get("runId"), "title": body.get("title"), "state": body.get("state") or body.get("status")}
        if response.get("status") != 201:
            record["error"] = response.get("error", ""); failures.append(record)
        results.append(record)
    print(json.dumps({"submittedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "apiBase": args.base_url, "repository": args.repository, "targetRuntime": args.runtime, "preset": {"slug": "jira-implement", "version": args.preset_version}, "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}}, "results": results, "failures": failures}, indent=2))
    return 1 if failures else 0

if __name__ == "__main__":
    raise SystemExit(main())
