#!/usr/bin/env python3
"""
Retrieve all comments posted on a GitHub pull request.

By default this collects:
- Issue comments on the PR conversation
- Inline review comments
- Review body comments (optional, enabled by default; empty bodies skipped)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def parse_repo_slug(slug: str) -> tuple[str, str]:
    match = re.match(r"^([^/\s]+)/([^/\s]+)$", slug.strip())
    if not match:
        raise ValueError(f"Invalid --repo value '{slug}'. Expected format: owner/repo")
    return match.group(1), match.group(2)


def detect_repo_from_git() -> tuple[str, str] | None:
    try:
        remote_bytes = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            stderr=subprocess.STDOUT,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    remote_url = remote_bytes.decode("utf-8").strip()
    match = re.search(r"(?:[:/])([^/]+)/([^/.]+)(?:\.git)?$", remote_url)
    if not match:
        return None

    return match.group(1), match.group(2)


def resolve_token(cli_token: str | None) -> str | None:
    if cli_token:
        return cli_token

    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    try:
        token = (
            subprocess.check_output(["gh", "auth", "token"], stderr=subprocess.STDOUT)
            .decode("utf-8")
            .strip()
        )
        return token or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def api_get_json(url: str, token: str | None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "moonmind-get-pr-comments",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", "replace")
        except Exception:
            # Leave the response body empty when decoding the error payload fails.
            pass
        raise RuntimeError(
            f"GitHub API request failed ({exc.code} {exc.reason}) for {url}\n{error_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error while calling {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON returned from {url}: {exc}") from exc


def fetch_paginated(url: str, token: str | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        separator = "&" if "?" in url else "?"
        paged_url = f"{url}{separator}per_page=100&page={page}"
        payload = api_get_json(paged_url, token)
        if not isinstance(payload, list):
            raise RuntimeError(
                f"Expected list payload from {paged_url}, got {type(payload).__name__}"
            )

        items.extend(payload)
        if len(payload) < 100:
            break
        page += 1

    return items


def normalize_issue_comment(comment: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "issue_comment",
        "id": comment.get("id"),
        "user": (comment.get("user") or {}).get("login"),
        "body": comment.get("body", ""),
        "created_at": comment.get("created_at"),
        "updated_at": comment.get("updated_at"),
        "url": comment.get("html_url"),
    }


def normalize_review_comment(
    comment: dict[str, Any],
    *,
    thread_states: dict[int, dict[str, bool]] | None = None,
) -> dict[str, Any]:
    cid = comment.get("id")
    ts = (thread_states or {}).get(cid, {}) if isinstance(cid, int) else {}
    return {
        "type": "review_comment",
        "id": cid,
        "user": (comment.get("user") or {}).get("login"),
        "body": comment.get("body", ""),
        "created_at": comment.get("created_at"),
        "updated_at": comment.get("updated_at"),
        "url": comment.get("html_url"),
        "path": comment.get("path"),
        "commit_id": comment.get("commit_id"),
        "in_reply_to_id": comment.get("in_reply_to_id"),
        "line": comment.get("line"),
        "side": comment.get("side"),
        "thread_resolved": ts.get("resolved", False),
        "thread_outdated": ts.get("outdated", False),
    }


def normalize_review(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "review",
        "id": review.get("id"),
        "user": (review.get("user") or {}).get("login"),
        "state": review.get("state"),
        "body": review.get("body", ""),
        "created_at": review.get("submitted_at") or review.get("created_at"),
        "updated_at": review.get("submitted_at") or review.get("updated_at"),
        "url": review.get("html_url"),
    }


_REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          isOutdated
          comments(first: 1) {
            nodes { databaseId }
          }
        }
      }
    }
  }
}
"""


def fetch_review_thread_states(
    owner: str,
    repo: str,
    pr_number: int,
    token: str | None,
) -> dict[int, dict[str, bool]]:
    """Fetch isResolved/isOutdated for every review thread via GraphQL.

    Returns a mapping from review-comment database ID to
    ``{"resolved": bool, "outdated": bool}``.
    """
    if not token:
        return {}

    url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "moonmind-get-pr-comments",
    }

    result: dict[int, dict[str, bool]] = {}
    cursor: str | None = None

    for _ in range(20):  # pagination safety cap
        variables: dict[str, Any] = {
            "owner": owner,
            "repo": repo,
            "number": pr_number,
        }
        if cursor:
            variables["cursor"] = cursor

        body = json.dumps({"query": _REVIEW_THREADS_QUERY, "variables": variables})
        request = urllib.request.Request(
            url, data=body.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
            # GraphQL enrichment is best-effort; fall back to no data.
            return result

        data = (payload.get("data") or {})
        repo_node = data.get("repository") or {}
        pr_node = repo_node.get("pullRequest") or {}
        threads_node = pr_node.get("reviewThreads") or {}
        nodes = threads_node.get("nodes") or []

        for thread in nodes:
            if not isinstance(thread, dict):
                continue
            is_resolved = bool(thread.get("isResolved", False))
            is_outdated = bool(thread.get("isOutdated", False))
            comments_nodes = (thread.get("comments") or {}).get("nodes") or []
            for comment_node in comments_nodes:
                if not isinstance(comment_node, dict):
                    continue
                db_id = comment_node.get("databaseId")
                if isinstance(db_id, int):
                    result[db_id] = {
                        "resolved": is_resolved,
                        "outdated": is_outdated,
                    }

        page_info = threads_node.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retrieve all comments posted on a GitHub pull request."
    )
    parser.add_argument("pr_number", type=int, help="Pull request number")
    parser.add_argument(
        "--repo",
        help="Repository in owner/repo format. If omitted, autodetect from git remote origin.",
    )
    parser.add_argument(
        "--token",
        help="GitHub token. Falls back to GITHUB_TOKEN then `gh auth token`.",
    )
    parser.add_argument(
        "--include-empty-reviews",
        action="store_true",
        help="Include review entries even if the review body is empty.",
    )
    parser.add_argument(
        "--exclude-reviews",
        action="store_true",
        help="Exclude top-level review body comments (keeps issue + inline review comments).",
    )
    parser.add_argument(
        "--output",
        help="Write JSON output to a file instead of stdout.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact JSON (default is pretty-printed).",
    )
    args = parser.parse_args()

    if args.repo:
        owner, repo = parse_repo_slug(args.repo)
    else:
        detected = detect_repo_from_git()
        if not detected:
            raise SystemExit(
                "Unable to detect repo from git. Please pass --repo owner/repo."
            )
        owner, repo = detected

    token = resolve_token(args.token)
    if not token:
        eprint(
            "Warning: No GitHub token found. Public repositories may still work with rate limits."
        )

    base = f"https://api.github.com/repos/{owner}/{repo}"
    pr_number = args.pr_number

    pr_metadata = api_get_json(f"{base}/pulls/{pr_number}", token)
    issue_comments_raw = fetch_paginated(f"{base}/issues/{pr_number}/comments", token)
    review_comments_raw = fetch_paginated(f"{base}/pulls/{pr_number}/comments", token)

    # Fetch thread resolution state via GraphQL
    thread_states = fetch_review_thread_states(owner, repo, pr_number, token)

    comments: list[dict[str, Any]] = []
    comments.extend(normalize_issue_comment(c) for c in issue_comments_raw)
    comments.extend(
        normalize_review_comment(c, thread_states=thread_states)
        for c in review_comments_raw
    )

    if not args.exclude_reviews:
        reviews_raw = fetch_paginated(f"{base}/pulls/{pr_number}/reviews", token)
        for review in reviews_raw:
            body = (review.get("body") or "").strip()
            if body or args.include_empty_reviews:
                comments.append(normalize_review(review))

    comments.sort(key=lambda c: (c.get("created_at") or "", c.get("id") or 0))

    result = {
        "repository": f"{owner}/{repo}",
        "pr_number": pr_number,
        "pr_title": pr_metadata.get("title"),
        "pr_url": pr_metadata.get("html_url"),
        "comment_count": len(comments),
        "comments": comments,
    }

    json_output = json.dumps(
        result,
        separators=(",", ":") if args.compact else None,
        indent=None if args.compact else 2,
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(json_output)
            handle.write("\n")
        eprint(f"Wrote {len(comments)} comments to {args.output}")
    else:
        print(json_output)


if __name__ == "__main__":
    main()
