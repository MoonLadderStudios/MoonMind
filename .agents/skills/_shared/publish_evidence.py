#!/usr/bin/env python3
"""Write canonical MoonMind auto-publish evidence from portable skill tooling."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

SCHEMA_VERSION = "moonmind.publish.auto.v1"
DEFAULT_ARTIFACTS_DIR = Path("artifacts")
DEFAULT_RESULT_PATH = DEFAULT_ARTIFACTS_DIR / "publish_result.json"
DEFAULT_PR_RESOLVER_SNAPSHOT_PATH = Path("var/pr_resolver/snapshot.json")


class PublishEvidenceError(RuntimeError):
    """Raised when canonical publish evidence cannot be proven."""


def _run_text(cmd: list[str], *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishEvidenceError(f"command unavailable: {cmd[0]}") from exc
    if result.returncode != 0:
        raise PublishEvidenceError(f"command failed: {' '.join(cmd[:3])}")
    return (result.stdout or "").strip()


def _run_json(cmd: list[str], *, timeout: int = 60) -> dict[str, Any]:
    output = _run_text(cmd, timeout=timeout)
    try:
        payload = json.loads(output or "{}")
    except json.JSONDecodeError as exc:
        raise PublishEvidenceError(
            f"command returned invalid JSON: {' '.join(cmd[:3])}"
        ) from exc
    if not isinstance(payload, dict):
        raise PublishEvidenceError(
            f"command returned non-object JSON: {' '.join(cmd[:3])}"
        )
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublishEvidenceError(f"JSON artifact not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PublishEvidenceError(f"JSON artifact is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise PublishEvidenceError(f"JSON artifact must be an object: {path}")
    return payload


def _read_json_optional(path: Path) -> dict[str, Any]:
    try:
        return _read_json(path)
    except PublishEvidenceError:
        return {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _first_text(*values: object) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _nested_mapping(payload: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _github_repository_from_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return ""
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) >= 2:
        return f"{segments[0]}/{segments[1].removesuffix('.git')}"
    return ""


def _repository_from_git_remote() -> str:
    try:
        remote = _run_text(["git", "remote", "get-url", "origin"])
    except PublishEvidenceError:
        return ""
    if remote.startswith("git@github.com:"):
        path = remote.split("git@github.com:", 1)[1]
        return path.removesuffix(".git").strip("/")
    if remote.startswith("https://") or remote.startswith("http://"):
        parsed = urlparse(remote)
        if parsed.hostname and parsed.hostname.lower() == "github.com":
            segments = [segment for segment in parsed.path.split("/") if segment]
            if len(segments) >= 2:
                return f"{segments[0]}/{segments[1].removesuffix('.git')}"
    return ""


def _local_head_required() -> str:
    return _run_text(["git", "rev-parse", "HEAD"])


def _local_head_optional() -> str:
    try:
        return _local_head_required()
    except PublishEvidenceError:
        return ""


def _current_branch_optional() -> str:
    try:
        branch = _run_text(["git", "branch", "--show-current"])
    except PublishEvidenceError:
        return ""
    return "" if branch == "HEAD" else branch


def _remote_branch_head(branch: str) -> str:
    if not branch:
        raise PublishEvidenceError("branch is required for remote head verification")
    output = _run_text(["git", "ls-remote", "origin", f"refs/heads/{branch}"])
    first_line = output.splitlines()[0] if output else ""
    sha = first_line.split()[0] if first_line.split() else ""
    if not sha:
        raise PublishEvidenceError(f"remote branch head not found for {branch}")
    return sha


def _remote_branch_head_optional(branch: str) -> str:
    try:
        return _remote_branch_head(branch)
    except PublishEvidenceError:
        return ""


def _verify_exact_remote_head(branch: str) -> tuple[str, str, list[str]]:
    local_head = _local_head_required()
    remote_head = _remote_branch_head(branch)
    if local_head != remote_head:
        raise PublishEvidenceError(f"local HEAD does not match origin/{branch}")
    return (
        local_head,
        remote_head,
        [
            "git rev-parse HEAD",
            f"git ls-remote origin refs/heads/{branch}",
        ],
    )


def _verify_pr_merged(pr_url: str) -> tuple[dict[str, Any], list[str]]:
    if not pr_url:
        raise PublishEvidenceError("prUrl is required for merged publish evidence")
    command = [
        "gh",
        "pr",
        "view",
        pr_url,
        "--json",
        "state,mergedAt,mergeCommit,url,headRefName,headRefOid",
    ]
    payload = _run_json(command)
    if _text(payload.get("state")).upper() != "MERGED":
        raise PublishEvidenceError("PR is not merged")
    if not _text(payload.get("mergedAt")) and not payload.get("mergeCommit"):
        raise PublishEvidenceError("merged PR verification is incomplete")
    return payload, [
        "gh pr view <pr-url> --json state,mergedAt,mergeCommit,url,headRefName,headRefOid"
    ]


def _evidence_payload(
    *,
    skill_id: str,
    status: str,
    action: str,
    repository: str,
    branch: str,
    local_head: str | None,
    remote_branch_head: str | None,
    remote_verified: bool,
    pushed: bool,
    merged: bool,
    pr_url: str | None = None,
    blocked_reason: str | None = None,
    verification_commands: list[str] | None = None,
) -> dict[str, Any]:
    if not _text(skill_id):
        raise PublishEvidenceError("skill-id is required")
    if not _text(repository):
        raise PublishEvidenceError("repo is required")
    if not _text(branch):
        raise PublishEvidenceError("branch is required")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "auto",
        "owner": "agent",
        "skillId": skill_id,
        "status": status,
        "action": action,
        "repository": repository,
        "branch": branch,
        "localHead": local_head or None,
        "remoteBranchHead": remote_branch_head or None,
        "remoteVerified": bool(remote_verified),
        "pushed": bool(pushed),
        "merged": bool(merged),
        "prUrl": pr_url or None,
        "blockedReason": blocked_reason or None,
        "verificationCommands": list(verification_commands or []),
    }


def _write_payload(payload: Mapping[str, Any], *, artifacts_dir: Path) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / "publish_result.json"
    path.write_text(json.dumps(dict(payload), indent=2) + "\n", encoding="utf-8")
    return path


def write_pushed(*, skill_id: str, repo: str, branch: str, artifacts_dir: Path) -> Path:
    local_head, remote_head, commands = _verify_exact_remote_head(branch)
    return _write_payload(
        _evidence_payload(
            skill_id=skill_id,
            status="verified",
            action="push",
            repository=repo,
            branch=branch,
            local_head=local_head,
            remote_branch_head=remote_head,
            remote_verified=True,
            pushed=True,
            merged=False,
            verification_commands=commands,
        ),
        artifacts_dir=artifacts_dir,
    )


def write_no_op(*, skill_id: str, repo: str, branch: str, artifacts_dir: Path) -> Path:
    local_head, remote_head, commands = _verify_exact_remote_head(branch)
    return _write_payload(
        _evidence_payload(
            skill_id=skill_id,
            status="no_op_verified",
            action="none",
            repository=repo,
            branch=branch,
            local_head=local_head,
            remote_branch_head=remote_head,
            remote_verified=True,
            pushed=False,
            merged=False,
            verification_commands=commands,
        ),
        artifacts_dir=artifacts_dir,
    )


def write_merged(
    *,
    skill_id: str,
    repo: str,
    branch: str,
    pr_url: str,
    artifacts_dir: Path,
) -> Path:
    gh_payload, commands = _verify_pr_merged(pr_url)
    local_head = _local_head_optional() or _text(gh_payload.get("headRefOid")) or None
    return _write_payload(
        _evidence_payload(
            skill_id=skill_id,
            status="verified",
            action="merge",
            repository=repo,
            branch=branch,
            local_head=local_head,
            remote_branch_head=None,
            remote_verified=True,
            pushed=False,
            merged=True,
            pr_url=pr_url,
            verification_commands=commands,
        ),
        artifacts_dir=artifacts_dir,
    )


def write_blocked(
    *,
    skill_id: str,
    repo: str,
    branch: str,
    reason: str,
    artifacts_dir: Path,
) -> Path:
    return _write_payload(
        _evidence_payload(
            skill_id=skill_id,
            status="blocked",
            action="none",
            repository=repo,
            branch=branch,
            local_head=_local_head_optional() or None,
            remote_branch_head=_remote_branch_head_optional(branch) or None,
            remote_verified=False,
            pushed=False,
            merged=False,
            blocked_reason=reason,
            verification_commands=[],
        ),
        artifacts_dir=artifacts_dir,
    )


def write_failed(
    *,
    skill_id: str,
    repo: str,
    branch: str,
    reason: str,
    artifacts_dir: Path,
) -> Path:
    return _write_payload(
        _evidence_payload(
            skill_id=skill_id,
            status="failed",
            action="none",
            repository=repo,
            branch=branch,
            local_head=_local_head_optional() or None,
            remote_branch_head=_remote_branch_head_optional(branch) or None,
            remote_verified=False,
            pushed=False,
            merged=False,
            blocked_reason=reason,
            verification_commands=[],
        ),
        artifacts_dir=artifacts_dir,
    )


def _snapshot_pr(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return snapshot.get("pr") if isinstance(snapshot.get("pr"), dict) else {}


def _result_final(result: Mapping[str, Any]) -> dict[str, Any]:
    return _nested_mapping(
        result,
        "final",
        "final_state",
        "finalState",
        "merge",
        "mergeOutcome",
    )


def _resolver_disposition(result: Mapping[str, Any]) -> str:
    final = _result_final(result)
    return _first_text(
        result.get("mergeAutomationDisposition"),
        result.get("merge_automation_disposition"),
        result.get("disposition"),
        final.get("mergeAutomationDisposition"),
        final.get("merge_automation_disposition"),
        final.get("disposition"),
    ).lower()


def _resolver_status(result: Mapping[str, Any]) -> str:
    final = _result_final(result)
    return _first_text(
        result.get("status"),
        result.get("state"),
        result.get("merge_outcome"),
        result.get("mergeOutcome"),
        final.get("status"),
        final.get("state"),
        final.get("merge_outcome"),
        final.get("mergeOutcome"),
    ).lower()


def _resolver_reason(result: Mapping[str, Any]) -> str:
    final = _result_final(result)
    return _first_text(
        result.get("final_reason"),
        result.get("reason"),
        result.get("decision"),
        final.get("final_reason"),
        final.get("reason"),
        final.get("decision"),
        "unknown",
    )


def _resolver_pr_url(result: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    final = _result_final(result)
    pr = _snapshot_pr(snapshot)
    return _first_text(
        result.get("prUrl"),
        result.get("pr_url"),
        result.get("url"),
        final.get("prUrl"),
        final.get("pr_url"),
        final.get("url"),
        pr.get("url"),
    )


def _resolver_repo(result: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    final = _result_final(result)
    pr_url = _resolver_pr_url(result, snapshot)
    repo = _first_text(
        result.get("repository"),
        result.get("repo"),
        final.get("repository"),
        final.get("repo"),
        _github_repository_from_url(pr_url),
    )
    return repo or _repository_from_git_remote() or "unknown/unknown"


def _resolver_branch(result: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    final = _result_final(result)
    pr = _snapshot_pr(snapshot)
    branch = _first_text(
        result.get("branch"),
        result.get("headBranch"),
        result.get("head_branch"),
        final.get("branch"),
        final.get("headBranch"),
        final.get("head_branch"),
        pr.get("headRefName"),
    )
    return branch or _current_branch_optional() or "unknown"


def _resolver_head(result: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    final = _result_final(result)
    pr = _snapshot_pr(snapshot)
    head = _first_text(
        result.get("localHead"),
        result.get("headSha"),
        result.get("head_sha"),
        final.get("localHead"),
        final.get("headSha"),
        final.get("head_sha"),
        pr.get("headRefOid"),
    )
    return head or _local_head_optional()


def _is_no_work_result(result: Mapping[str, Any]) -> bool:
    disposition = _resolver_disposition(result)
    status = _resolver_status(result)
    reason = _resolver_reason(result).lower()
    return (
        disposition in {"no_op", "no_work", "no_work_needed", "already_current"}
        or status in {"no_op", "noop", "no_work", "no_work_needed"}
        or reason
        in {
            "no_op",
            "noop",
            "no_work",
            "no_work_needed",
            "already_current",
            "already_up_to_date",
        }
    )


def from_pr_resolver_result(
    *,
    result_path: Path,
    snapshot_path: Path,
    artifacts_dir: Path,
) -> Path:
    result = _read_json(result_path)
    snapshot = _read_json_optional(snapshot_path)
    disposition = _resolver_disposition(result)
    status = _resolver_status(result)
    reason = _resolver_reason(result)
    repo = _resolver_repo(result, snapshot)
    branch = _resolver_branch(result, snapshot)
    pr_url = _resolver_pr_url(result, snapshot)

    if disposition in {"merged", "already_merged"} or status == "merged":
        if not pr_url:
            return write_blocked(
                skill_id="pr-resolver",
                repo=repo,
                branch=branch,
                reason="pr_url_unavailable",
                artifacts_dir=artifacts_dir,
            )
        try:
            return write_merged(
                skill_id="pr-resolver",
                repo=repo,
                branch=branch,
                pr_url=pr_url,
                artifacts_dir=artifacts_dir,
            )
        except PublishEvidenceError:
            return write_blocked(
                skill_id="pr-resolver",
                repo=repo,
                branch=branch,
                reason="remote_merge_verification_unavailable",
                artifacts_dir=artifacts_dir,
            )

    if disposition == "reenter_gate":
        try:
            local_head, remote_head, commands = _verify_exact_remote_head(branch)
        except PublishEvidenceError:
            return write_blocked(
                skill_id="pr-resolver",
                repo=repo,
                branch=branch,
                reason="remote_verification_unavailable",
                artifacts_dir=artifacts_dir,
            )
        return _write_payload(
            _evidence_payload(
                skill_id="pr-resolver",
                status="verified",
                action="push",
                repository=repo,
                branch=branch,
                local_head=local_head,
                remote_branch_head=remote_head,
                remote_verified=True,
                pushed=True,
                merged=False,
                pr_url=pr_url or None,
                verification_commands=commands,
            ),
            artifacts_dir=artifacts_dir,
        )

    if _is_no_work_result(result):
        try:
            local_head, remote_head, commands = _verify_exact_remote_head(branch)
        except PublishEvidenceError:
            return write_blocked(
                skill_id="pr-resolver",
                repo=repo,
                branch=branch,
                reason="remote_verification_unavailable",
                artifacts_dir=artifacts_dir,
            )
        return _write_payload(
            _evidence_payload(
                skill_id="pr-resolver",
                status="no_op_verified",
                action="none",
                repository=repo,
                branch=branch,
                local_head=local_head,
                remote_branch_head=remote_head,
                remote_verified=True,
                pushed=False,
                merged=False,
                pr_url=pr_url or None,
                verification_commands=commands,
            ),
            artifacts_dir=artifacts_dir,
        )

    if disposition == "failed" or status == "failed":
        return write_failed(
            skill_id="pr-resolver",
            repo=repo,
            branch=branch,
            reason=reason,
            artifacts_dir=artifacts_dir,
        )

    return write_blocked(
        skill_id="pr-resolver",
        repo=repo,
        branch=branch,
        reason=reason
        if (
            status in {"blocked", "attempts_exhausted"}
            or disposition in {"manual_review", "blocked"}
        )
        else f"unsupported_pr_resolver_state:{status or disposition or 'unknown'}",
        artifacts_dir=artifacts_dir,
    )


def _add_common_write_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--skill-id", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--artifacts-dir", default=str(DEFAULT_ARTIFACTS_DIR))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write canonical MoonMind auto-publish evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pushed = subparsers.add_parser("write-pushed")
    _add_common_write_args(pushed)

    merged = subparsers.add_parser("write-merged")
    _add_common_write_args(merged)
    merged.add_argument("--pr-url", required=True)

    no_op = subparsers.add_parser("write-no-op")
    _add_common_write_args(no_op)

    blocked = subparsers.add_parser("write-blocked")
    _add_common_write_args(blocked)
    blocked.add_argument("--reason", required=True)

    failed = subparsers.add_parser("write-failed")
    _add_common_write_args(failed)
    failed.add_argument("--reason", required=True)

    resolver = subparsers.add_parser("from-pr-resolver-result")
    resolver.add_argument("--result", required=True)
    resolver.add_argument(
        "--snapshot",
        default=str(DEFAULT_PR_RESOLVER_SNAPSHOT_PATH),
        help="Optional pr-resolver snapshot artifact used to fill repo/branch/PR metadata.",
    )
    resolver.add_argument("--artifacts-dir", default=str(DEFAULT_ARTIFACTS_DIR))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifacts_dir = Path(getattr(args, "artifacts_dir", DEFAULT_ARTIFACTS_DIR))
    try:
        if args.command == "write-pushed":
            path = write_pushed(
                skill_id=args.skill_id,
                repo=args.repo,
                branch=args.branch,
                artifacts_dir=artifacts_dir,
            )
        elif args.command == "write-merged":
            path = write_merged(
                skill_id=args.skill_id,
                repo=args.repo,
                branch=args.branch,
                pr_url=args.pr_url,
                artifacts_dir=artifacts_dir,
            )
        elif args.command == "write-no-op":
            path = write_no_op(
                skill_id=args.skill_id,
                repo=args.repo,
                branch=args.branch,
                artifacts_dir=artifacts_dir,
            )
        elif args.command == "write-blocked":
            path = write_blocked(
                skill_id=args.skill_id,
                repo=args.repo,
                branch=args.branch,
                reason=args.reason,
                artifacts_dir=artifacts_dir,
            )
        elif args.command == "write-failed":
            path = write_failed(
                skill_id=args.skill_id,
                repo=args.repo,
                branch=args.branch,
                reason=args.reason,
                artifacts_dir=artifacts_dir,
            )
        elif args.command == "from-pr-resolver-result":
            path = from_pr_resolver_result(
                result_path=Path(args.result),
                snapshot_path=Path(args.snapshot),
                artifacts_dir=artifacts_dir,
            )
        else:
            raise PublishEvidenceError(f"unsupported command: {args.command}")
    except PublishEvidenceError as exc:
        print(f"publish evidence error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
