"""Check dependency fetchability using the URLs a fresh consumer will use."""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from moonmind.utils.logging import redact_sensitive_text


class SubmodulePublicationError(RuntimeError):
    """A dependency could not be verified before publishing its consumer."""


def _text(value: str | bytes) -> str:
    return (
        value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    )


async def verify_submodule_publication(
    *,
    repo_dir: Path,
    candidate_ref: str,
    base_ref: str | None,
    run_git: Callable[..., Awaitable[Any]],
    remote_url: str | None = None,
) -> None:
    """Verify each new gitlink against its committed declared remote.

    The caller supplies its existing Git runner and credential boundary. Local
    submodule checkouts, cached remote refs, and URL overrides are not evidence.
    Git itself resolves relative URLs in a temporary consumer; dependency
    commits are fetched into temporary bare repositories, never published.
    """

    async def checked(*args: str, cwd: Path = repo_dir, network: bool = False):
        result = await run_git(*args, cwd=cwd, network=network)
        if result.returncode != 0:
            detail = redact_sensitive_text(_text(result.stderr).strip())[:500]
            raise SubmodulePublicationError(
                f"submodule publication verification failed: git {args[0]}: {detail}"
            )
        return _text(result.stdout)

    revisions = await checked(
        "rev-list", "--reverse", candidate_ref, *([f"^{base_ref}"] if base_ref else [])
    )
    changes: list[tuple[str, dict[str, str]]] = []
    for revision in revisions.splitlines():
        raw = await checked(
            "diff-tree",
            "--root",
            "-r",
            "-m",
            "--no-commit-id",
            "--raw",
            "--no-abbrev",
            "--no-renames",
            "-z",
            revision,
        )
        entries = raw.rstrip("\0").split("\0") if raw else []
        links: dict[str, str] = {}
        modules_changed = False
        for metadata, path in zip(entries[::2], entries[1::2]):
            _old_mode, new_mode, _old_sha, new_sha, _status = metadata.split()
            if new_mode == "160000":
                links[path] = new_sha
            modules_changed |= path == ".gitmodules"
        if modules_changed:
            # A URL change can invalidate an otherwise unchanged dependency SHA.
            tree = await checked("ls-tree", "-r", "-z", revision)
            for entry in tree.split("\0"):
                if entry:
                    metadata, path = entry.split("\t", 1)
                    mode, _kind, sha = metadata.split()
                    if mode == "160000":
                        links[path] = sha
        if links:
            changes.append((revision, links))
    if not changes:
        return

    if remote_url is None:
        remote_url = (await checked("remote", "get-url", "origin")).strip()
    with tempfile.TemporaryDirectory(prefix="moonmind-submodule-check-") as root:
        consumer = Path(root) / "consumer"
        await checked(
            "clone",
            "--shared",
            "--no-checkout",
            "--no-recurse-submodules",
            "--",
            str(repo_dir.resolve()),
            str(consumer),
        )
        await checked("config", "remote.origin.url", remote_url, cwd=consumer)
        verified: set[tuple[str, str]] = set()
        for revision, links in changes:
            mapping = await checked(
                "config",
                f"--blob={revision}:.gitmodules",
                "--null",
                "--get-regexp",
                r"^submodule\..*\.path$",
            )
            names = {}
            for entry in mapping.split("\0"):
                if entry:
                    key, path = entry.split("\n", 1)
                    names[path] = key[len("submodule.") : -len(".path")]
            await checked("read-tree", revision, cwd=consumer)
            await checked("checkout", revision, "--", ".gitmodules", cwd=consumer)
            for path, sha in links.items():
                name = names.get(path)
                if name is None:
                    raise SubmodulePublicationError(
                        f"submodule {path} has no committed .gitmodules declaration"
                    )
                # Discard only the temporary consumer's previous URL resolution.
                await run_git(
                    "config", "--remove-section", f"submodule.{name}", cwd=consumer
                )
                await checked("submodule", "init", "--", path, cwd=consumer)
                url = (
                    await checked(
                        "config", "--get", f"submodule.{name}.url", cwd=consumer
                    )
                ).strip()
                if (url, sha) in verified:
                    continue
                dependency = Path(root) / f"dependency-{len(verified)}"
                await checked(
                    "init",
                    "--bare",
                    "--template=",
                    f"--object-format={'sha256' if len(sha) == 64 else 'sha1'}",
                    str(dependency),
                )
                fetched = await run_git(
                    "fetch",
                    "--no-tags",
                    "--depth=1",
                    "--",
                    url,
                    sha,
                    cwd=dependency,
                    network=True,
                )
                if fetched.returncode != 0:
                    detail = redact_sensitive_text(
                        _text(fetched.stderr).strip().replace(url, "[declared remote]")
                    )[:500]
                    missing = any(
                        marker in detail.lower()
                        for marker in (
                            "not our ref",
                            "couldn't find remote ref",
                            "unadvertised object",
                        )
                    )
                    reason = (
                        f"submodule {path} commit {sha} could not be found on its declared remote"
                        if missing
                        else f"submodule {path} remote verification unavailable"
                    )
                    raise SubmodulePublicationError(f"{reason}: {detail}")
                await checked("cat-file", "-e", f"{sha}^{{commit}}", cwd=dependency)
                verified.add((url, sha))
