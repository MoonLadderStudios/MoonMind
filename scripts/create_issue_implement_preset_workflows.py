#!/usr/bin/env python3
"""Create issue implementation preset executions for Jira or GitHub issues."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

DEFAULT_REPOSITORY = "MoonLadderStudios/MoonMind"
DEFAULT_RUNTIME = "codex_cli"
DEFAULT_PRESET_VERSION = "1.0.0"
PROVIDER_DEFAULT_PRESET_VERSIONS = {"jira": "1.1.0", "github": "1.0.0"}
PROVIDER_PRESETS = {"jira": "jira-implement", "github": "github-issue-implement"}


def normalize_issue_ref(*, provider: str, issue: str, repository: str) -> tuple[str, dict[str, Any]]:
    if provider == "jira":
        issue_key = issue.strip().upper()
        return issue_key, {"jira_issue": {"key": issue_key}, "constraints": ""}
    raw = issue.strip()
    repo = repository.strip()
    if raw.startswith("#"):
        number_text = raw[1:]
    elif "#" in raw:
        repo, number_text = raw.rsplit("#", 1)
        repo = repo.strip("/")
    else:
        number_text = raw
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 4 and parts[2] == "issues":
            repo = f"{parts[0]}/{parts[1]}"
            number_text = parts[3]
    number = int(number_text)
    ref = f"{repo}#{number}"
    return ref, {"github_issue": {"repository": repo, "number": number}, "constraints": ""}


def build_idempotency_key(*, provider: str, issue_ref: str, repository: str, runtime: str, preset_version: str) -> str:
    preset_slug = PROVIDER_PRESETS[provider]
    return (
        f"{preset_slug}:{issue_ref}:{repository.strip()}:{runtime.strip()}:"
        f"{preset_version.strip()}:pr-merge-automation-expanded-steps"
    )


def build_payload(*, provider: str, issue_ref: str, repository: str, runtime: str, expanded_steps: list[dict[str, Any]], applied_template: dict[str, Any] | None = None, preset_version: str = DEFAULT_PRESET_VERSION) -> dict[str, Any]:
    preset_slug = PROVIDER_PRESETS[provider]
    title_provider = "Jira" if provider == "jira" else "GitHub Issue"
    task: dict[str, Any] = {
        "title": f"Run {title_provider} Implement for {issue_ref}",
        "instructions": f"Run {title_provider} Implement for {issue_ref}.\n\nUse the existing {title_provider} Implement workflow for this issue.",
        "runtime": {"mode": runtime},
        "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
        "inputs": normalize_issue_ref(provider=provider, issue=issue_ref, repository=repository)[1],
        "steps": expanded_steps,
        "taskTemplate": {"slug": preset_slug, "version": preset_version, "scope": "global"},
        "presetSchedule": {
            "source": "batch",
            "reason": f"{provider}_issue_batch",
            "presetSlug": preset_slug,
            "presetVersion": preset_version,
            "issueProvider": provider,
            "issueRef": issue_ref,
        },
    }
    if provider == "jira":
        task["inputs"]["jira_issue_key"] = issue_ref
        task["presetSchedule"]["jiraIssueKey"] = issue_ref
    if applied_template:
        task["appliedStepTemplates"] = [applied_template]
    return {
        "type": "task",
        "payload": {
            "repository": repository,
            "targetRuntime": runtime,
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "idempotencyKey": build_idempotency_key(provider=provider, issue_ref=issue_ref, repository=repository, runtime=runtime, preset_version=preset_version),
            "integration": provider,
            "task": task,
        },
    }


def build_expand_payload(*, provider: str, issue: str, repository: str, runtime: str, preset_version: str = DEFAULT_PRESET_VERSION) -> dict[str, Any]:
    _ref, inputs = normalize_issue_ref(provider=provider, issue=issue, repository=repository)
    return {
        "version": preset_version,
        "inputs": inputs,
        "context": {"repository": repository, "targetRuntime": runtime},
        "options": {"enforceStepLimit": True},
    }


def post_json(*, base_url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(f"{base_url.rstrip('/')}/api/executions", data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return {"status": response.status, "body": json.loads(body) if body else {}}
    except error.HTTPError as exc:
        return {"status": exc.code, "error": exc.read().decode("utf-8", errors="replace")}
    except Exception as exc:
        return {"status": 0, "error": str(exc)}


def expand_issue_implement(*, base_url: str, provider: str, issue: str, repository: str, runtime: str, preset_version: str, timeout: float) -> dict[str, Any]:
    preset_slug = PROVIDER_PRESETS[provider]
    payload = build_expand_payload(provider=provider, issue=issue, repository=repository, runtime=runtime, preset_version=preset_version)
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(f"{base_url.rstrip('/')}/api/presets/{preset_slug}:expand?scope=global", data=data, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create issue implementation preset workflows with PR merge automation enabled.")
    parser.add_argument("issues", nargs="+", help="Jira keys, GitHub issue URLs, owner/repo#123, or #123 with --repository.")
    parser.add_argument("--provider", choices=sorted(PROVIDER_PRESETS), default="jira")
    parser.add_argument("--base-url", default=os.environ.get("MOONMIND_URL", "http://api:8000"))
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--runtime", default=DEFAULT_RUNTIME)
    parser.add_argument("--preset-version", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.preset_version is None:
        args.preset_version = PROVIDER_DEFAULT_PRESET_VERSIONS[args.provider]
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for raw_issue in args.issues:
        try:
            issue_ref, _inputs = normalize_issue_ref(provider=args.provider, issue=raw_issue, repository=args.repository)
        except Exception as exc:
            record = {"issue": raw_issue, "status": 0, "error": f"Invalid issue reference: {exc}"}
            failures.append(record); results.append(record); continue
        if args.dry_run:
            results.append({"issue": issue_ref, "expandPayload": build_expand_payload(provider=args.provider, issue=raw_issue, repository=args.repository, runtime=args.runtime, preset_version=args.preset_version)})
            continue
        try:
            expanded = expand_issue_implement(base_url=args.base_url, provider=args.provider, issue=raw_issue, repository=args.repository, runtime=args.runtime, preset_version=args.preset_version, timeout=args.timeout)
        except Exception as exc:
            record = {"issue": issue_ref, "status": 0, "error": f"Expansion failed: {exc}"}
            failures.append(record); results.append(record); continue
        raw_steps = expanded.get("steps")
        if not isinstance(raw_steps, list) or len(raw_steps) != 8:
            record = {"issue": issue_ref, "error": f"Expected {PROVIDER_PRESETS[args.provider]} expansion to produce 8 steps; got {len(raw_steps) if isinstance(raw_steps, list) else 0}."}
            failures.append(record); results.append(record); continue
        payload = build_payload(provider=args.provider, issue_ref=issue_ref, repository=args.repository, runtime=args.runtime, expanded_steps=raw_steps, applied_template=expanded.get("appliedTemplate") if isinstance(expanded.get("appliedTemplate"), dict) else None, preset_version=args.preset_version)
        response = post_json(base_url=args.base_url, payload=payload, timeout=args.timeout)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        record = {"issue": issue_ref, "status": response.get("status"), "workflowId": body.get("workflowId") or body.get("id"), "runId": body.get("runId"), "title": body.get("title"), "state": body.get("state") or body.get("status")}
        if response.get("status") != 201:
            record["error"] = response.get("error", ""); failures.append(record)
        results.append(record)
    print(json.dumps({"submittedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "apiBase": args.base_url, "repository": args.repository, "targetRuntime": args.runtime, "preset": {"slug": PROVIDER_PRESETS[args.provider], "version": args.preset_version}, "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}}, "results": results, "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
