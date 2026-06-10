"""High-security pre-push scanning for MoonMind-owned git side effects."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from moonmind.security.outbound_scan import (
    OutboundScanResult,
    resolve_high_security_mode,
    scan_outbound_bundle,
)
from moonmind.utils.logging import redact_sensitive_text

GitCommandRunner = Callable[[list[str]], Awaitable[str]]
GitCommandRunnerWithCwd = Callable[..., Awaitable[str]]

_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_MAX_SCAN_ITEM_BYTES = 256_000


class GitPushScanBlockedError(RuntimeError):
    """Raised when a pre-push scan blocks the push side effect."""

    def __init__(
        self,
        *,
        branch: str,
        diagnostics: list[str],
        result: OutboundScanResult,
    ) -> None:
        self.branch = branch
        self.diagnostics = diagnostics
        self.result = result
        detail = "; ".join(diagnostics) or "blocked outbound git push content"
        super().__init__(
            f"Outbound git push blocked by high security scan"
            f" (branch={branch or 'unknown'}): {detail}"
        )


class GitPushScanMaterializationError(RuntimeError):
    """Raised when high-security mode cannot materialize the outbound range."""


async def scan_git_push_range_before_push(
    *,
    repo_dir: Path,
    branch: str,
    run_git: GitCommandRunnerWithCwd,
    base_ref: str | None = None,
    git_binary: str = "git",
    high_security_mode: bool | None = None,
    max_item_bytes: int = _MAX_SCAN_ITEM_BYTES,
) -> OutboundScanResult:
    """Scan the outbound commit range before invoking git push.

    Git range materialization is intentionally skipped when high-security mode is
    off so disabled mode preserves existing push behavior and cost.
    """

    if not resolve_high_security_mode(high_security_mode):
        return scan_outbound_bundle([], high_security_mode=False)

    repo_path = Path(repo_dir)
    normalized_branch = str(branch or "").strip() or "unknown"
    try:
        range_spec, diff_base = await _resolve_scan_range(
            repo_dir=repo_path,
            base_ref=base_ref,
            run_git=run_git,
            git_binary=git_binary,
        )
        commit_count_text = await run_git(
            [git_binary, "rev-list", "--count", range_spec],
            cwd=repo_path,
        )
        try:
            commit_count = int(str(commit_count_text).strip() or "0")
        except ValueError:
            commit_count = 0
        if commit_count <= 0:
            return scan_outbound_bundle([], high_security_mode=True)

        metadata = await run_git(
            [
                git_binary,
                "log",
                "--format=commit %H%nsubject %s%nbody %b%n",
                range_spec,
            ],
            cwd=repo_path,
        )
        diff = await run_git(
            [
                git_binary,
                "diff",
                "--find-renames",
                "--no-ext-diff",
                f"{diff_base}..HEAD",
            ],
            cwd=repo_path,
        )
    except GitPushScanBlockedError:
        raise
    except Exception as exc:
        detail = redact_sensitive_text(str(exc))
        raise GitPushScanMaterializationError(
            "failed to materialize outbound git push range "
            f"for branch {normalized_branch}: {detail}"
        ) from exc

    result = scan_outbound_bundle(
        [
            {
                "location": f"git.commit.metadata:{normalized_branch}",
                "content": _limit_text(metadata, max_bytes=max_item_bytes),
            },
            {
                "location": f"git.diff:{normalized_branch}",
                "content": _limit_text(diff, max_bytes=max_item_bytes),
            },
        ],
        high_security_mode=True,
    )
    if result.allowed:
        return result

    raise GitPushScanBlockedError(
        branch=normalized_branch,
        diagnostics=list(result.sanitized_diagnostics),
        result=result,
    )


async def _resolve_scan_range(
    *,
    repo_dir: Path,
    base_ref: str | None,
    run_git: GitCommandRunnerWithCwd,
    git_binary: str,
) -> tuple[str, str]:
    candidate = str(base_ref or "").strip()
    if candidate and await _rev_exists(
        repo_dir=repo_dir,
        ref=candidate,
        run_git=run_git,
        git_binary=git_binary,
    ):
        return f"{candidate}..HEAD", candidate
    return "HEAD", _EMPTY_TREE_SHA


async def _rev_exists(
    *,
    repo_dir: Path,
    ref: str,
    run_git: GitCommandRunnerWithCwd,
    git_binary: str,
) -> bool:
    try:
        await run_git(
            [git_binary, "rev-parse", "--verify", "--quiet", ref],
            cwd=repo_dir,
        )
    except Exception:
        return False
    return True


def _limit_text(text: str, *, max_bytes: int) -> str:
    encoded = str(text or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(text or "")
    clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{clipped}\n[moonmind: pre-push scan payload truncated]"
