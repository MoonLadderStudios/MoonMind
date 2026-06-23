#!/usr/bin/env python3
"""Create issue implementation preset executions for Jira or GitHub issues."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any
from urllib import error, request
from urllib.parse import quote, urlparse

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


def build_idempotency_key(*, provider: str, issue_ref: str, repository: str, runtime: str, preset_version: str, model: str | None = None, effort: str | None = None) -> str:
    preset_slug = PROVIDER_PRESETS[provider]
    parts = [preset_slug, issue_ref, repository.strip(), runtime.strip(), preset_version.strip()]
    model = (model or "").strip()
    effort = (effort or "").strip()
    if model or effort:
        # The persisted idempotency column is varchar(128). Encode runtime model/effort
        # shaping as a short fixed-length token so distinct shaping stays distinct without
        # overflowing the column for long model identifiers.
        digest = hashlib.sha256(f"{model}|{effort}".encode("utf-8")).hexdigest()[:10]
        parts.append(f"rt-{digest}")
    parts.append("pr-merge-automation-expanded-steps")
    return ":".join(parts)


def build_runtime_block(*, runtime: str, model: str | None = None, effort: str | None = None) -> dict[str, Any]:
    """Build the task runtime selection block, honoring optional model/effort overrides.

    The execution router reads ``task.runtime.model`` and ``task.runtime.effort`` and
    passes them through to the agent runtime byte-for-byte (Compatibility Policy), so
    only include them when explicitly requested.
    """
    runtime_block: dict[str, Any] = {"mode": runtime}
    if model:
        runtime_block["model"] = model
    if effort:
        runtime_block["effort"] = effort
    return runtime_block


def build_payload(*, provider: str, issue_ref: str, repository: str, runtime: str, expanded_steps: list[dict[str, Any]], applied_template: dict[str, Any] | None = None, preset_version: str = DEFAULT_PRESET_VERSION, model: str | None = None, effort: str | None = None) -> dict[str, Any]:
    preset_slug = PROVIDER_PRESETS[provider]
    title_provider = "Jira" if provider == "jira" else "GitHub Issue"
    task: dict[str, Any] = {
        "title": f"Run {title_provider} Implement for {issue_ref}",
        "instructions": f"Run {title_provider} Implement for {issue_ref}.\n\nUse the existing {title_provider} Implement workflow for this issue.",
        "runtime": build_runtime_block(runtime=runtime, model=model, effort=effort),
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
            "idempotencyKey": build_idempotency_key(provider=provider, issue_ref=issue_ref, repository=repository, runtime=runtime, preset_version=preset_version, model=model, effort=effort),
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


def fetch_board_issues(*, base_url: str, board_id: str, project_key: str | None, timeout: float) -> dict[str, Any]:
    """Fetch a Jira board's issues grouped by column from the MoonMind API."""
    url = f"{base_url.rstrip('/')}/api/jira/boards/{quote(board_id)}/issues"
    if project_key:
        url += f"?projectKey={quote(project_key)}"
    req = request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_column_issue_keys(board_issues: dict[str, Any], column_name: str) -> list[str]:
    """Return the issue keys in the column whose human-readable name matches ``column_name``.

    Matching is case-insensitive and whitespace-trimmed. Column ids are slugified
    server-side (e.g. ``"To Do"`` -> ``"to-do"``) so we resolve ids by name and then
    collect the keys from ``itemsByColumn`` for every matching column, preserving order
    and de-duplicating.
    """
    target = column_name.strip().casefold()
    columns = board_issues.get("columns") or []
    matching_ids = [
        str(column.get("id"))
        for column in columns
        if isinstance(column, dict) and str(column.get("name") or "").strip().casefold() == target
    ]
    items_by_column = board_issues.get("itemsByColumn") or {}
    keys: list[str] = []
    seen: set[str] = set()
    for column_id in matching_ids:
        for item in items_by_column.get(column_id) or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("issueKey") or "").strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create issue implementation preset workflows with PR merge automation enabled.")
    parser.add_argument("issues", nargs="*", help="Jira keys, GitHub issue URLs, owner/repo#123, or #123 with --repository. Optional when --board-id is given.")
    parser.add_argument("--provider", choices=sorted(PROVIDER_PRESETS), default="jira")
    parser.add_argument("--base-url", default=os.environ.get("MOONMIND_URL", "http://api:8000"))
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--runtime", default=DEFAULT_RUNTIME)
    parser.add_argument("--model", default=None, help="Runtime model override, e.g. claude-opus-4-8 (passed through task.runtime.model).")
    parser.add_argument("--effort", default=None, help="Runtime effort override, e.g. xhigh (passed through task.runtime.effort).")
    parser.add_argument("--board-id", default=None, help="Discover issues from this Jira board id instead of (or in addition to) positional args.")
    parser.add_argument("--column", default="To Do", help="Board column name to pull issues from when --board-id is set (default: 'To Do').")
    parser.add_argument("--project-key", default=None, help="Optional Jira project key passed as the projectKey query param for board discovery.")
    parser.add_argument("--preset-version", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.preset_version is None:
        args.preset_version = PROVIDER_DEFAULT_PRESET_VERSIONS[args.provider]

    issues: list[str] = list(args.issues)
    discovery: dict[str, Any] | None = None
    if args.board_id:
        try:
            board_issues = fetch_board_issues(base_url=args.base_url, board_id=args.board_id, project_key=args.project_key, timeout=args.timeout)
            if not isinstance(board_issues, dict):
                raise TypeError(f"Expected API response to be a dictionary, got {type(board_issues).__name__}")
            discovered = extract_column_issue_keys(board_issues, args.column)
        except Exception as exc:
            print(json.dumps({"error": f"Failed to fetch board {args.board_id} issues: {exc}"}, indent=2))
            return 1
        merged: list[str] = []
        seen: set[str] = set()
        for key in [*discovered, *issues]:
            if key and key not in seen:
                seen.add(key)
                merged.append(key)
        issues = merged
        discovery = {"boardId": args.board_id, "column": args.column, "discoveredIssues": discovered}

    if not issues:
        target = f"column {args.column!r} of board {args.board_id}" if args.board_id else "the provided arguments"
        print(json.dumps({"error": f"No issues to queue from {target}.", "discovery": discovery}, indent=2))
        return 1

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for raw_issue in issues:
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
        payload = build_payload(provider=args.provider, issue_ref=issue_ref, repository=args.repository, runtime=args.runtime, expanded_steps=raw_steps, applied_template=expanded.get("appliedTemplate") if isinstance(expanded.get("appliedTemplate"), dict) else None, preset_version=args.preset_version, model=args.model, effort=args.effort)
        response = post_json(base_url=args.base_url, payload=payload, timeout=args.timeout)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        record = {"issue": issue_ref, "status": response.get("status"), "workflowId": body.get("workflowId") or body.get("id"), "runId": body.get("runId"), "title": body.get("title"), "state": body.get("state") or body.get("status")}
        if response.get("status") != 201:
            record["error"] = response.get("error", ""); failures.append(record)
        results.append(record)
    print(json.dumps({"submittedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "apiBase": args.base_url, "repository": args.repository, "targetRuntime": args.runtime, "model": args.model, "effort": args.effort, "discovery": discovery, "preset": {"slug": PROVIDER_PRESETS[args.provider], "version": args.preset_version}, "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}}, "results": results, "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
