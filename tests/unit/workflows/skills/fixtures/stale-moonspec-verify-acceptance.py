#!/usr/bin/env python3
"""Portable subject capture and evidence reuse for moonspec-verify.

No provider API, MoonMind import, or credential discovery. Callers perform the
authorized fetch first and supply the actual source and completion target.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    return (
        subprocess.check_output(
            ["git", "-C", str(repo), *args], env=env, stderr=subprocess.PIPE
        )
        .decode()
        .strip()
    )


def nonblank_strings(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def capture(
    repo: Path, repository: str, target: str, *, target_mode: bool = False
) -> dict:
    """Capture actual content, including unstaged/untracked work, without checkout."""
    repo = Path(git(repo, "rev-parse", "--show-toplevel"))
    target_revision = git(repo, "rev-parse", "--verify", f"{target}^{{commit}}")
    target_tree = git(repo, "rev-parse", f"{target_revision}^{{tree}}")
    target_ref = git(repo, "rev-parse", "--symbolic-full-name", target)
    if target_ref.startswith("refs/remotes/"):
        target_ref = (
            "refs/heads/" + target_ref.removeprefix("refs/remotes/").split("/", 1)[1]
        )
    if not target_ref.startswith("refs/heads/"):
        raise ValueError(
            "completion policy must name a branch, not a detached revision"
        )
    revision = target_revision if target_mode else git(repo, "rev-parse", "HEAD")
    if target_mode:
        tree = target_tree
    else:
        # A private index leaves the user's staging intact. Git's ignore rules
        # exclude disposable test artifacts; real untracked source is included.
        with tempfile.TemporaryDirectory(prefix="acceptance-index-") as temp:
            original_objects = Path(git(repo, "rev-parse", "--git-path", "objects"))
            if not original_objects.is_absolute():
                original_objects = repo / original_objects
            temporary_objects = Path(temp) / "objects"
            temporary_objects.mkdir()
            env = dict(
                os.environ,
                GIT_INDEX_FILE=str(Path(temp) / "index"),
                GIT_OBJECT_DIRECTORY=str(temporary_objects),
                GIT_ALTERNATE_OBJECT_DIRECTORIES=str(original_objects),
                GIT_OPTIONAL_LOCKS="0",
            )
            git(repo, "read-tree", "HEAD", env=env)
            git(repo, "add", "--all", "--", ".", env=env)
            # Inspect every staged gitlink, including embedded repositories
            # absent from .gitmodules. A gitlink cannot bind dirty nested
            # source to the captured tree.
            for entry in git(repo, "ls-files", "--stage", "-z", env=env).split("\0"):
                if not entry.startswith("160000 "):
                    continue
                nested = repo / entry.split("\t", 1)[1]
                if (nested / ".git").exists():
                    dirty = git(
                        nested,
                        "--no-optional-locks",
                        "status",
                        "--porcelain",
                        "--untracked-files=all",
                        "--ignore-submodules=none",
                    )
                else:
                    # An empty uninitialized submodule has no local content.
                    # Populated paths without their owning Git metadata cannot
                    # be certified by the recorded revision alone.
                    dirty = nested.is_dir() and any(nested.iterdir())
                if dirty:
                    raise ValueError(
                        "dirty gitlinks require a content-bound workspace checkpoint"
                    )
            tree = git(repo, "write-tree", env=env)
    return {
        "subject": {
            "repository": repository,
            "revision": revision,
            "contentDigest": f"git-tree:{tree}",
        },
        "completionTarget": {
            "ref": target_ref,
            "revision": target_revision,
            "contentDigest": f"git-tree:{target_tree}",
        },
    }


def scope(
    source: bytes, source_ref: str, requirement_ids: list[str], *, complete: bool
) -> dict:
    if not nonblank_strings(requirement_ids) or len(set(requirement_ids)) != len(
        requirement_ids
    ):
        raise ValueError(
            "supply the unique mandatory requirement IDs from the original scope"
        )
    return {
        "sourceRef": source_ref,
        "sourceDigest": "sha256:" + hashlib.sha256(source).hexdigest(),
        "requirementIds": sorted(requirement_ids),
        "complete": complete,
    }


def reuse(report: dict, current: dict, *, now: datetime | None = None) -> dict:
    """Check an objective report against freshly captured subject/scope/policy."""
    try:
        return _reuse(report, current, now=now)
    except (AttributeError, KeyError, TypeError, ValueError):
        return {
            "reusable": False,
            "completionEligible": False,
            "reasons": [
                "malformed acceptance binding; obtain complete objective evidence"
            ],
        }


def _reuse(report: dict, current: dict, *, now: datetime | None = None) -> dict:
    evidence = report.get("validatedRefs", {}).get("acceptance", {})
    reasons = []
    if (
        report.get("verdict") != "FULLY_IMPLEMENTED"
        or report.get("invalid")
        or report.get("degraded")
    ):
        reasons.append("no accepted objective verdict")
    if evidence.get("schemaVersion") != "acceptance/v1":
        reasons.append("objective acceptance binding missing")
    prior_subject = evidence.get("subject", {})
    subject = current.get("subject", {})
    # A commit containing identical content (including a squash merge) can
    # reuse content evidence. The report retains the original tested revision.
    for key in ("repository", "contentDigest"):
        if not subject.get(key) or prior_subject.get(key) != subject[key]:
            reasons.append(f"subject {key} changed or missing")
    if not prior_subject.get("revision") or not subject.get("revision"):
        reasons.append("revision identity missing")
    current_scope = current.get("scope", {})
    if (
        current_scope.get("complete") is not True
        or evidence.get("scope") != current_scope
    ):
        reasons.append("source scope changed or incomplete")
    required = current_scope.get("requirementIds", [])
    rows = evidence.get("evidence", [])
    if (
        not nonblank_strings(required)
        or len(set(required)) != len(required)
        or sorted(row.get("requirementId", "") for row in rows) != sorted(required)
        or any(not nonblank_strings(row.get("evidenceRefs")) for row in rows)
    ):
        reasons.append("mandatory requirement evidence missing")
    target = current.get("completionTarget", {})
    if (
        not target.get("ref")
        or evidence.get("completionTarget", {}).get("ref") != target["ref"]
    ):
        reasons.append("completion policy changed or missing")
    freshness = evidence.get("freshness", {})
    if (
        not current.get("freshnessPolicy")
        or freshness.get("policy") != current["freshnessPolicy"]
    ):
        reasons.append("required evidence freshness changed or missing")
    if freshness.get("validUntil"):
        try:
            expiry = datetime.fromisoformat(
                freshness["validUntil"].replace("Z", "+00:00")
            )
            if expiry.tzinfo is None or (now or datetime.now(timezone.utc)) >= expiry:
                reasons.append("evidence expired")
        except (TypeError, ValueError):
            reasons.append("invalid evidence expiry")
    reusable = not reasons
    return {
        "reusable": reusable,
        "completionEligible": bool(
            reusable
            and target.get("revision")
            and target.get("contentDigest") == subject.get("contentDigest")
        ),
        "reasons": reasons,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--repo", type=Path, default=Path.cwd())
    capture_parser.add_argument("--repository", required=True)
    capture_parser.add_argument("--target", required=True)
    capture_parser.add_argument("--target-mode", action="store_true")
    capture_parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Original source and constraints, preserved verbatim",
    )
    capture_parser.add_argument("--source-ref", required=True)
    capture_parser.add_argument("--requirement", action="append", required=True)
    capture_parser.add_argument("--incomplete", action="store_true")
    capture_parser.add_argument("--freshness-policy", default="content")
    reuse_parser = commands.add_parser("reuse")
    reuse_parser.add_argument("--report", type=Path, required=True)
    reuse_parser.add_argument("--current", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture":
        result = capture(
            args.repo, args.repository, args.target, target_mode=args.target_mode
        )
        result["scope"] = scope(
            args.source.read_bytes(),
            args.source_ref,
            args.requirement,
            complete=not args.incomplete,
        )
        result["freshnessPolicy"] = args.freshness_policy
    else:
        result = reuse(
            json.loads(args.report.read_text()), json.loads(args.current.read_text())
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
