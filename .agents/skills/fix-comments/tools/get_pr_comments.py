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
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

def eprint(message: str) -> None:
    print(message, file=sys.stderr)

def parse_repo_slug(slug: str) -> tuple[str, str]:
    match = _parse_remote_url(slug)
    if match:
        return match

    match = re.match(r"^([^/\s]+)/([^/\s]+)$", slug.strip())
    if not match:
        raise ValueError(f"Invalid --repo value '{slug}'. Expected format: owner/repo")
    return match.group(1), match.group(2)

def _split_owner_repo_path(path: str) -> tuple[str, str] | None:
    trimmed = path.strip().strip("/")
    if not trimmed:
        return None

    parts = [part for part in trimmed.split("/") if part]
    if len(parts) < 2:
        return None

    owner = parts[0].strip()
    repo = parts[1].strip().removesuffix(".git")
    if not owner or not repo:
        return None
    return owner, repo

def _parse_remote_url(remote_url: str) -> tuple[str, str] | None:
    candidate = remote_url.strip()
    if not candidate:
        return None

    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme and parsed.netloc:
        return _split_owner_repo_path(parsed.path)

    # SSH/scp-style syntax: git@host:owner/repo.git
    scp_match = re.match(r"^(?:[^@/\s]+@)?[^:/\s]+:(.+)$", candidate)
    if scp_match:
        return _split_owner_repo_path(scp_match.group(1))

    return None

def detect_repo_from_git() -> tuple[str, str] | None:
    try:
        remote_bytes = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            stderr=subprocess.STDOUT,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    remote_url = remote_bytes.decode("utf-8").strip()
    return _parse_remote_url(remote_url)

def resolve_token(cli_token: str | None) -> str | None:
    if cli_token:
        return cli_token

    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(env_name):
            return os.environ[env_name]

    try:
        token = (
            subprocess.check_output(["gh", "auth", "token"], stderr=subprocess.STDOUT)
            .decode("utf-8")
            .strip()
        )
        return token or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

def is_retryable_http_status(status: int) -> bool:
    return status == 429 or status >= 500

def api_get_json(
    url: str,
    token: str | None,
    max_attempts: int = 3,
    initial_delay_seconds: float = 1.0,
    max_delay_seconds: float = 8.0,
) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "tactics-get-pr-comments",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers, method="GET")
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            if attempt < max_attempts and is_retryable_http_status(exc.code):
                delay = min(
                    max_delay_seconds, initial_delay_seconds * (2 ** (attempt - 1))
                )
                eprint(
                    f"Retryable API response on attempt {attempt}/{max_attempts}: "
                    f"{exc.code} {exc.reason}. Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
                continue

            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", "replace")
            except Exception as read_exc:
                eprint(
                    f"Warning: Failed to read GitHub API error response body: {read_exc}"
                )
            raise RuntimeError(
                f"GitHub API request failed ({exc.code} {exc.reason}) for {url}\n{error_body or 'No error body available.'}"
            ) from exc
        except urllib.error.URLError as exc:
            if attempt < max_attempts:
                delay = min(
                    max_delay_seconds, initial_delay_seconds * (2 ** (attempt - 1))
                )
                eprint(
                    f"Retryable network error on attempt {attempt}/{max_attempts} for {url}: "
                    f"{exc.reason}. Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
                continue
            raise RuntimeError(
                f"Network error while calling {url}: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON returned from {url}: {exc}") from exc

    raise RuntimeError(f"Failed to fetch {url} after {max_attempts} attempts")

def api_post_json(
    url: str,
    payload: dict[str, Any],
    token: str | None,
    max_attempts: int = 3,
    initial_delay_seconds: float = 1.0,
    max_delay_seconds: float = 8.0,
) -> Any:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "tactics-get-pr-comments",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            if attempt < max_attempts and is_retryable_http_status(exc.code):
                delay = min(
                    max_delay_seconds, initial_delay_seconds * (2 ** (attempt - 1))
                )
                eprint(
                    f"Retryable API response on attempt {attempt}/{max_attempts}: "
                    f"{exc.code} {exc.reason}. Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
                continue

            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", "replace")
            except Exception as read_exc:
                eprint(
                    f"Warning: Failed to read GitHub API error response body: {read_exc}"
                )
            raise RuntimeError(
                f"GitHub API POST failed ({exc.code} {exc.reason}) for {url}\n{error_body or 'No error body available.'}"
            ) from exc
        except urllib.error.URLError as exc:
            if attempt < max_attempts:
                delay = min(
                    max_delay_seconds, initial_delay_seconds * (2 ** (attempt - 1))
                )
                eprint(
                    f"Retryable network error on attempt {attempt}/{max_attempts} for {url}: "
                    f"{exc.reason}. Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
                continue
            raise RuntimeError(
                f"Network error while calling {url}: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON returned from {url}: {exc}") from exc

    raise RuntimeError(f"Failed to POST {url} after {max_attempts} attempts")

def fetch_review_thread_status(
    owner: str, repo: str, pr_number: int, token: str | None
) -> dict[int, dict[str, bool]]:
    """Fetch isResolved/isOutdated for each review thread comment via GraphQL.

    Returns a mapping of comment database ID to {isResolved, isOutdated}.
    Returns {} on any failure so callers gracefully fall back.
    """
    if not token:
        return {}

    query = """
    query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr) {
          reviewThreads(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              isResolved
              isOutdated
              comments(first: 100) {
                nodes { databaseId }
              }
            }
          }
        }
      }
    }
    """

    result: dict[int, dict[str, bool]] = {}
    cursor: str | None = None

    try:
        while True:
            variables: dict[str, Any] = {
                "owner": owner,
                "repo": repo,
                "pr": pr_number,
            }
            if cursor:
                variables["cursor"] = cursor

            response = api_post_json(
                "https://api.github.com/graphql",
                {"query": query, "variables": variables},
                token,
            )

            threads_data = (
                response.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
            )
            nodes = threads_data.get("nodes", [])

            for thread in nodes:
                is_resolved = thread.get("isResolved", False)
                is_outdated = thread.get("isOutdated", False)
                for comment in thread.get("comments", {}).get("nodes", []):
                    db_id = comment.get("databaseId")
                    if db_id is not None:
                        result[db_id] = {
                            "isResolved": is_resolved,
                            "isOutdated": is_outdated,
                        }

            page_info = threads_data.get("pageInfo", {})
            if page_info.get("hasNextPage"):
                cursor = page_info.get("endCursor")
            else:
                break

    except Exception as exc:
        eprint(f"Warning: GraphQL thread status fetch failed: {exc}")
        return {}

    return result

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
    thread_status: dict[int, dict[str, bool]] | None = None,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "type": "review_comment",
        "id": comment.get("id"),
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
    }
    if thread_status is not None:
        comment_id = comment.get("id")
        status = thread_status.get(comment_id) if comment_id else None
        if status:
            normalized["thread_resolved"] = status["isResolved"]
            normalized["thread_outdated"] = status["isOutdated"]
    return normalized

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
        help="GitHub token. Falls back to GITHUB_TOKEN, GH_TOKEN, then `gh auth token`.",
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

    thread_status = fetch_review_thread_status(owner, repo, pr_number, token)

    comments: list[dict[str, Any]] = []
    comments.extend(normalize_issue_comment(c) for c in issue_comments_raw)
    comments.extend(
        normalize_review_comment(c, thread_status) for c in review_comments_raw
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
        out_path = os.path.abspath(args.output)
        if not out_path.startswith(os.path.abspath(".")):
            raise PermissionError("Output path must be within the current directory")
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(json_output)
            handle.write("\n")
        eprint(f"Wrote {len(comments)} comments to {out_path}")
    else:
        print(json_output)

if __name__ == "__main__":
    main()
