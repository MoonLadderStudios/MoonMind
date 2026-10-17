#!/usr/bin/env python3
"""Commit staged remediation work and record a truthful local failure.

A commit that Git refuses is a local workspace failure, not a publication
outage: the PR, its remote branch and GitHub authority are untouched. This
helper commits exactly the staged index, diagnoses a refusal from Git's own
structured state (identity resolution, and the hook or signing program Git's
trace2 events record as having failed) and writes ``local_commit_failed`` with
the bounded, redacted original Git error so the resolver neither claims
``publish_unavailable`` nor reruns completed remediation once the condition is
corrected.

It never accepts an author, never bypasses hooks or signing, never amends and
never pushes. Rerunning it reconciles against the recorded receipt first, so a
commit that already landed is not created twice.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pr_resolve_contract import (  # noqa: E402
    EXIT_CODE_FAILED,
    LOCAL_COMMIT_FAILED_REASON,
    LOCAL_COMMIT_MAX_ATTEMPTS,
    RESULT_SCHEMA_VERSION,
    current_execution_ref,
    local_commit_next_step,
    merge_automation_disposition_for_result,
    normalize_text,
    now_utc_iso,
)

EXIT_CODE_COMMITTED = 0
EXIT_CODE_NOTHING_TO_COMMIT = 1
DEFAULT_RESULT_PATH = Path("var/pr_resolver/result.json")
GIT_ERROR_MAX_CHARS = 2000
GIT_TIMEOUT_SECONDS = 300
EXIT_CODE_GIT_TIMEOUT = 124
# Only hooks that run before the commit object exists can refuse it.
_REJECTING_HOOKS = ("pre-commit", "prepare-commit-msg", "commit-msg")
# Hook and signing-program output is persisted, so credential shapes that a
# helper commonly prints are removed before the error is stored or emitted.
_SECRET_PATTERNS = (
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
            r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
            re.S,
        ),
        "[redacted]",
    ),
    (re.compile(r"(?i)\b(authorization\s*:\s*)(?:[a-z]+\s+)?\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[redacted]"),
    (
        re.compile(
            r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]+"
            r"|\bAIza[A-Za-z0-9_-]{16,}|\bAKIA[A-Z0-9]{16}\b"
        ),
        "[redacted]",
    ),
    (
        re.compile(
            r"(?i)\b([a-z0-9_.-]{0,80}(?:token|passw(?:or)?d|secret|api[_-]?key"
            r"|credential)[a-z0-9_.-]{0,80})\s*[:=]\s*"
            r"(?:\"[^\"]*\"|'[^']*'|[^\s,;\"']+)"
        ),
        r"\1=[redacted]",
    ),
    (re.compile(r"(://)[^/@\s]+@"), r"\1[redacted]@"),
)


def _git(
    *args: str, input_text: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        # A hung hook or signing program is a local failure with its own
        # captured output, not a missing receipt.
        def _text(value: str | bytes | None) -> str:
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return value or ""

        stderr = _text(exc.stderr).rstrip()
        stderr += f"\ngit {args[0]} timed out after {GIT_TIMEOUT_SECONDS} seconds"
        return subprocess.CompletedProcess(
            exc.cmd, EXIT_CODE_GIT_TIMEOUT, _text(exc.stdout), stderr.lstrip()
        )


def _git_value(*args: str) -> str:
    result = _git(*args)
    return result.stdout.strip() if result.returncode == 0 else ""


def _bounded_git_error(result: subprocess.CompletedProcess) -> str:
    text = "\n".join(
        part.strip() for part in (result.stderr, result.stdout) if part and part.strip()
    )
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > GIT_ERROR_MAX_CHARS:
        text = "..." + text[-(GIT_ERROR_MAX_CHARS - 3) :]
    return text


def _commit_hooks() -> list[str]:
    configured = _git_value("config", "--get", "core.hooksPath")
    if configured:
        hooks_dir = Path(configured).expanduser()
        if not hooks_dir.is_absolute():
            hooks_dir = Path(_git_value("rev-parse", "--show-toplevel")) / hooks_dir
    else:
        hooks_dir = Path(_git_value("rev-parse", "--git-path", "hooks"))
    return [
        name
        for name in _REJECTING_HOOKS
        if (hooks_dir / name).is_file() and os.access(hooks_dir / name, os.X_OK)
    ]


def _read_trace(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _failed_child(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the start event of the last child the commit process saw fail.

    Hooks and their own Git commands append nested sessions to the same trace;
    only the commit process's session (the file's first event) is read.
    """

    if not events:
        return {}
    session = events[0].get("sid")
    started: dict[Any, dict[str, Any]] = {}
    failed: dict[str, Any] = {}
    for event in events:
        if event.get("sid") != session:
            continue
        if event.get("event") == "child_start":
            started[event.get("child_id")] = event
        elif event.get("event") == "child_exit" and event.get("code") != 0:
            failed = started.get(event.get("child_id"), {})
    return failed


def _is_signing_program(child: dict[str, Any]) -> bool:
    argv = child.get("argv")
    if not isinstance(argv, list):
        return False
    # Git's own argv shapes for OpenPGP/X.509 and SSH signing.
    return "-bsau" in argv or argv[1:3] == ["-Y", "sign"]


def diagnose_commit_failure(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify a refused commit from Git state rather than its message text.

    Configured signing or an installed hook proves nothing on its own: Git
    can refuse earlier (a held index lock, an unwritable object database).
    Only the child Git records as failing names the stage.
    """

    missing_roles = [
        role
        for role, variable in (
            ("author", "GIT_AUTHOR_IDENT"),
            ("committer", "GIT_COMMITTER_IDENT"),
        )
        if _git("var", variable).returncode != 0
    ]
    signing_required = (
        _git_value("config", "--type=bool", "--get", "commit.gpgsign") == "true"
    )
    hooks = _commit_hooks()
    failed = _failed_child(events)
    # Git resolves identity before running hooks or signing, so an unresolvable
    # identity is the refusal. Otherwise the failed child names the stage;
    # anything else stays ``other`` with its original error.
    if missing_roles:
        kind = "identity_missing"
    elif failed.get("child_class") == "hook":
        kind = "hook_rejected"
    elif _is_signing_program(failed):
        kind = "signing_failed"
    else:
        kind = "other"
    return {
        "kind": kind,
        "missingIdentityRoles": missing_roles,
        "signingRequired": signing_required,
        "commitHooks": hooks,
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _prior_receipt(previous: dict[str, Any]) -> dict[str, Any]:
    receipt = previous.get("localCommit")
    if normalize_text(
        previous.get("reason")
    ) != LOCAL_COMMIT_FAILED_REASON or not isinstance(receipt, dict):
        return {}
    return receipt


def _landed(head_before: str, staged_tree: str) -> str:
    """Return HEAD when it is exactly one commit of *staged_tree* on *head_before*."""

    head = _git_value("rev-parse", "--verify", "-q", "HEAD")
    if not head or head == head_before or not staged_tree:
        return ""
    parent = _git_value("rev-parse", "--verify", "-q", "HEAD^")
    if parent != head_before:
        return ""
    return head if _git_value("rev-parse", "HEAD^{tree}") == staged_tree else ""


def _emit(payload: dict[str, Any], code: int) -> None:
    print(json.dumps(payload, sort_keys=True))
    raise SystemExit(code)


def _write_failure(
    result_path: Path,
    *,
    previous: dict[str, Any],
    pr_selector: str,
    receipt: dict[str, Any],
) -> None:
    kind = receipt["kind"]
    next_step = local_commit_next_step(kind)
    first_line = next(
        (line for line in receipt["gitError"].splitlines() if line.strip()), ""
    )
    status = "blocked"
    merge_outcome = "blocked"
    pr_number = previous.get("pr_number")
    if pr_number is None and pr_selector.isdigit():
        pr_number = int(pr_selector)
    payload: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "executionRef": current_execution_ref(),
        "tool": "pr_resolve_commit",
        "timestamp": now_utc_iso(),
        "pr_number": pr_number,
        "pr_url": previous.get("pr_url"),
        "headSha": receipt["headBefore"] or None,
        "decision": (
            f"Local git commit failed ({kind}); nothing was published. {first_line}"
        ).strip(),
        "status": status,
        "merge_outcome": merge_outcome,
        "mergeAutomationDisposition": merge_automation_disposition_for_result(
            status=status,
            merge_outcome=merge_outcome,
            final_reason=LOCAL_COMMIT_FAILED_REASON,
            next_step=next_step,
        ),
        "reason": LOCAL_COMMIT_FAILED_REASON,
        "final_reason": LOCAL_COMMIT_FAILED_REASON,
        "next_step": next_step,
        "localCommit": receipt,
    }
    if isinstance(previous.get("attempt_history"), list):
        payload["attempt_history"] = previous["attempt_history"]
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--message",
        "-m",
        action="append",
        required=True,
        help="Commit message paragraph (repeatable, like git commit -m).",
    )
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT_PATH)
    parser.add_argument("--pr", default="", help="PR number, when known.")
    args = parser.parse_args(argv)

    if _git("rev-parse", "--git-dir").returncode != 0:
        parser.error("not inside a Git repository")
    head_before = _git_value("rev-parse", "--verify", "-q", "HEAD")
    tree = _git("write-tree")
    staged_tree = tree.stdout.strip() if tree.returncode == 0 else ""
    previous = _read_json(args.result)
    prior = _prior_receipt(previous)

    # Reconcile before repeating: a previous attempt may already have created
    # the commit even though it reported failure.
    recorded_tree = normalize_text(prior.get("stagedTree"))
    landed = recorded_tree == staged_tree and _landed(
        normalize_text(prior.get("headBefore")), recorded_tree
    )
    if landed:
        _emit({"status": "reconciled", "head": landed}, EXIT_CODE_COMMITTED)

    if head_before:
        base_tree = _git_value("rev-parse", f"{head_before}^{{tree}}")
    else:
        # The empty tree of this repository's object format (SHA-1 or SHA-256).
        empty = _git("hash-object", "-t", "tree", "--stdin", input_text="")
        base_tree = empty.stdout.strip() if empty.returncode == 0 else ""
    merging = bool(_git_value("rev-parse", "--verify", "-q", "MERGE_HEAD"))
    if staged_tree and staged_tree == base_tree and not merging:
        _emit(
            {"status": "nothing_to_commit", "head": head_before or None},
            EXIT_CODE_NOTHING_TO_COMMIT,
        )

    same_candidate = (
        normalize_text(prior.get("headBefore")) == head_before
        and normalize_text(prior.get("stagedTree")) == staged_tree
    )
    attempts = int(prior.get("attempts") or 0) if same_candidate else 0
    if attempts >= LOCAL_COMMIT_MAX_ATTEMPTS:
        receipt = {**prior, "exhausted": True}
        _write_failure(
            args.result, previous=previous, pr_selector=args.pr, receipt=receipt
        )
        _emit(
            {"status": "failed", "kind": prior.get("kind"), "exhausted": True},
            EXIT_CODE_FAILED,
        )

    message = "\n\n".join(part.strip() for part in args.message if part.strip())
    with tempfile.TemporaryDirectory(prefix="pr-resolve-commit-") as trace_dir:
        trace = Path(trace_dir) / "trace2.json"
        commit = _git(
            "commit",
            "--file",
            "-",
            input_text=message + "\n",
            env={**os.environ, "GIT_TRACE2_EVENT": str(trace)},
        )
        events = _read_trace(trace)
    if commit.returncode == 0:
        head = _git_value("rev-parse", "HEAD")
        _emit({"status": "committed", "head": head}, EXIT_CODE_COMMITTED)
    landed = _landed(head_before, staged_tree)
    if landed:
        _emit({"status": "reconciled", "head": landed}, EXIT_CODE_COMMITTED)

    receipt = {
        "phase": "commit",
        **diagnose_commit_failure(events),
        "exitCode": commit.returncode,
        "gitError": _bounded_git_error(commit),
        "headBefore": head_before,
        "headAfter": _git_value("rev-parse", "--verify", "-q", "HEAD"),
        "stagedTree": staged_tree,
        "branch": _git_value("symbolic-ref", "--short", "-q", "HEAD"),
        "attempts": attempts + 1,
        "exhausted": attempts + 1 >= LOCAL_COMMIT_MAX_ATTEMPTS,
    }
    _write_failure(args.result, previous=previous, pr_selector=args.pr, receipt=receipt)
    print(receipt["gitError"], file=sys.stderr)
    _emit({"status": "failed", "kind": receipt["kind"]}, EXIT_CODE_FAILED)


if __name__ == "__main__":
    main()
