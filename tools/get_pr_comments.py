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

	for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
		if os.environ.get(env_name):
			return os.environ[env_name]

	try:
		token = subprocess.check_output(["gh", "auth", "token"], stderr=subprocess.STDOUT).decode("utf-8").strip()
		return token or None
	except (subprocess.CalledProcessError, FileNotFoundError):
		return None


def api_get_json(url: str, token: str | None) -> Any:
	headers = {
		"Accept": "application/vnd.github+json",
		"User-Agent": "tactics-get-pr-comments",
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
			pass
		raise RuntimeError(f"GitHub API request failed ({exc.code} {exc.reason}) for {url}\n{error_body}") from exc
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
			raise RuntimeError(f"Expected list payload from {paged_url}, got {type(payload).__name__}")

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


def normalize_review_comment(comment: dict[str, Any]) -> dict[str, Any]:
	return {
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
	parser = argparse.ArgumentParser(description="Retrieve all comments posted on a GitHub pull request.")
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
			raise SystemExit("Unable to detect repo from git. Please pass --repo owner/repo.")
		owner, repo = detected

	token = resolve_token(args.token)
	if not token:
		eprint("Warning: No GitHub token found. Public repositories may still work with rate limits.")

	base = f"https://api.github.com/repos/{owner}/{repo}"
	pr_number = args.pr_number

	pr_metadata = api_get_json(f"{base}/pulls/{pr_number}", token)
	issue_comments_raw = fetch_paginated(f"{base}/issues/{pr_number}/comments", token)
	review_comments_raw = fetch_paginated(f"{base}/pulls/{pr_number}/comments", token)

	comments: list[dict[str, Any]] = []
	comments.extend(normalize_issue_comment(c) for c in issue_comments_raw)
	comments.extend(normalize_review_comment(c) for c in review_comments_raw)

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

	json_output = json.dumps(result, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2)
	if args.output:
		with open(args.output, "w", encoding="utf-8") as handle:
			handle.write(json_output)
			handle.write("\n")
		eprint(f"Wrote {len(comments)} comments to {args.output}")
	else:
		print(json_output)


if __name__ == "__main__":
	main()
