#!/usr/bin/env python3
"""Post a Jira comment through MoonMind's trusted MCP tool endpoint."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_*]+"),
    re.compile(r"github_pat_[A-Za-z0-9_*]+"),
    re.compile(r"ATATT[A-Za-z0-9_\-]+"),
    re.compile(r"AIza[A-Za-z0-9_\-]+"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(token|password)\s*="),
    re.compile(r"(?i)\bAuthorization\s*:"),
]

def _base_url(explicit: str | None) -> str:
    value = (
        explicit
        or os.environ.get("MOONMIND_API_BASE")
        or os.environ.get("MOONMIND_URL")
        or ""
    ).strip()
    if not value:
        raise SystemExit(
            "Missing MoonMind API base URL. Set MOONMIND_URL or pass --base-url."
        )
    return value.rstrip("/")

def _scan(body: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(body):
            raise SystemExit(
                f"Refusing to post comment: body matches secret pattern {pattern.pattern!r}."
            )

def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    auth_header = os.environ.get("MOONMIND_AUTH_HEADER", "").strip()
    bearer_token = (
        os.environ.get("MOONMIND_API_TOKEN")
        or os.environ.get("MOONMIND_AUTH_TOKEN")
        or os.environ.get("MOONMIND_BEARER_TOKEN")
        or ""
    ).strip()
    api_key = os.environ.get("MOONMIND_API_KEY", "").strip()
    if auth_header:
        headers["Authorization"] = auth_header
    elif bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if api_key:
        headers["X-API-Key"] = api_key
    return headers

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post a Jira comment via MoonMind's trusted jira.add_comment tool."
    )
    parser.add_argument("--issue", required=True, help="Jira issue key, e.g. ENG-123.")
    parser.add_argument("--body-file", required=True, help="Markdown comment file.")
    parser.add_argument(
        "--base-url",
        help="MoonMind API base URL. Defaults to MOONMIND_API_BASE or MOONMIND_URL.",
    )
    args = parser.parse_args()

    issue = args.issue.strip().upper()
    try:
        body = Path(args.body_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Error reading body file {args.body_file}: {exc}", file=sys.stderr)
        return 1
    _scan(body)

    payload = {
        "tool": "jira.add_comment",
        "arguments": {
            "issueKey": issue,
            "body": body,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{_base_url(args.base_url)}/mcp/tools/call",
        data=data,
        headers=_headers(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        print(
            f"jira.add_comment failed with HTTP {exc.code}: {response_body}",
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as exc:
        print(f"jira.add_comment request failed: {exc.reason}", file=sys.stderr)
        return 1

    print(response_body)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
