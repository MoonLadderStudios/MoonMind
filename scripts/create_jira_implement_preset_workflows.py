#!/usr/bin/env python3
"""Create Jira Implement preset executions for a list of Jira issue keys."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any
from urllib import error, request


DEFAULT_REPOSITORY = "MoonLadderStudios/MoonMind"
DEFAULT_RUNTIME = "codex_cli"
DEFAULT_PRESET_VERSION = "1.0.0"


def build_payload(
    *,
    issue_key: str,
    repository: str,
    runtime: str,
    expanded_steps: list[dict[str, Any]],
    applied_template: dict[str, Any] | None = None,
    preset_version: str = DEFAULT_PRESET_VERSION,
) -> dict[str, Any]:
    issue = issue_key.strip().upper()
    instructions = (
        f"Run Jira Implement for {issue}.\n\n"
        "Use the existing Jira Implement workflow for this Jira issue."
    )
    task: dict[str, Any] = {
        "title": f"Run Jira Implement for {issue}",
        "instructions": instructions,
        "runtime": {"mode": runtime},
        "publish": {
            "mode": "pr",
            "mergeAutomation": {"enabled": True},
        },
        "inputs": {
            "jira_issue_key": issue,
            "constraints": "",
        },
        "steps": expanded_steps,
        "taskTemplate": {
            "slug": "jira-implement",
            "version": preset_version,
            "scope": "global",
        },
        "presetSchedule": {
            "source": "batch",
            "reason": "jira_issue_batch",
            "presetSlug": "jira-implement",
            "presetVersion": preset_version,
            "jiraIssueKey": issue,
        },
    }
    if applied_template:
        task["appliedStepTemplates"] = [applied_template]

    return {
        "type": "task",
        "payload": {
            "repository": repository,
            "targetRuntime": runtime,
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "idempotencyKey": (
                f"jira-implement:{issue}:pr-merge-automation-expanded-steps"
            ),
            "integration": "jira",
            "task": task,
        },
    }


def build_expand_payload(
    *,
    issue_key: str,
    repository: str,
    runtime: str,
    preset_version: str = DEFAULT_PRESET_VERSION,
) -> dict[str, Any]:
    return {
        "version": preset_version,
        "inputs": {
            "jira_issue": {"key": issue_key.strip().upper()},
            "constraints": "",
        },
        "context": {
            "repository": repository,
            "targetRuntime": runtime,
        },
        "options": {"enforceStepLimit": True},
    }


def post_json(*, base_url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url.rstrip('/')}/api/executions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            return {"status": response.status, "body": parsed}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "error": body}


def expand_jira_implement(
    *,
    base_url: str,
    issue_key: str,
    repository: str,
    runtime: str,
    preset_version: str,
    timeout: float,
) -> dict[str, Any]:
    payload = build_expand_payload(
        issue_key=issue_key,
        repository=repository,
        runtime=runtime,
        preset_version=preset_version,
    )
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url.rstrip('/')}/api/task-step-templates/jira-implement:expand?scope=global",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create Jira Implement preset workflows with publish mode PR and "
            "merge automation enabled."
        )
    )
    parser.add_argument("issues", nargs="+", help="Jira issue keys, e.g. MM-770")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MOONMIND_URL", "http://api:8000"),
        help="MoonMind API base URL. Defaults to MOONMIND_URL or http://api:8000.",
    )
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--runtime", default=DEFAULT_RUNTIME)
    parser.add_argument("--preset-version", default=DEFAULT_PRESET_VERSION)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print request payloads without creating executions.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for issue in args.issues:
        expanded_steps: list[dict[str, Any]] = []
        applied_template: dict[str, Any] | None = None
        if args.dry_run:
            results.append(
                {
                    "issue": issue.upper(),
                    "expandPayload": build_expand_payload(
                        issue_key=issue,
                        repository=args.repository,
                        runtime=args.runtime,
                        preset_version=args.preset_version,
                    ),
                }
            )
            continue

        expanded = expand_jira_implement(
            base_url=args.base_url,
            issue_key=issue,
            repository=args.repository,
            runtime=args.runtime,
            preset_version=args.preset_version,
            timeout=args.timeout,
        )
        raw_steps = expanded.get("steps")
        if not isinstance(raw_steps, list) or len(raw_steps) != 8:
            failures.append(
                {
                    "issue": issue.upper(),
                    "error": (
                        "Expected jira-implement expansion to produce 8 steps; "
                        f"got {len(raw_steps) if isinstance(raw_steps, list) else 0}."
                    ),
                }
            )
            continue
        expanded_steps = raw_steps
        raw_applied_template = expanded.get("appliedTemplate")
        if isinstance(raw_applied_template, dict):
            applied_template = raw_applied_template
        payload = build_payload(
            issue_key=issue,
            repository=args.repository,
            runtime=args.runtime,
            expanded_steps=expanded_steps,
            applied_template=applied_template,
            preset_version=args.preset_version,
        )

        response = post_json(
            base_url=args.base_url,
            payload=payload,
            timeout=args.timeout,
        )
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        record = {
            "issue": issue.upper(),
            "status": response.get("status"),
            "workflowId": body.get("workflowId") or body.get("id"),
            "runId": body.get("runId"),
            "title": body.get("title"),
            "state": body.get("state") or body.get("status"),
        }
        if response.get("status") != 201:
            record["error"] = response.get("error", "")
            failures.append(record)
        results.append(record)

    report = {
        "submittedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "apiBase": args.base_url,
        "repository": args.repository,
        "targetRuntime": args.runtime,
        "preset": {"slug": "jira-implement", "version": args.preset_version},
        "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
        "results": results,
        "failures": failures,
    }
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
