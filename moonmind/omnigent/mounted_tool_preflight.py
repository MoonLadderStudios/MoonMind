"""Required mounted-tool readiness at the Omnigent host/runner boundary (MM-1215)."""

from __future__ import annotations

import json
import shlex
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moonmind.utils.logging import redact_sensitive_text


CommandRunner = Callable[..., Awaitable[tuple[int, str, str]]]
MAX_EVIDENCE_CHARS = 512


class MountedToolPreflightError(RuntimeError):
    """Stable, bounded failure raised before an Omnigent session is created."""

    def __init__(self, message: str, *, code: str, evidence: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.evidence = dict(evidence)


@dataclass(frozen=True)
class Probe:
    name: str
    command: str
    failure_code: str


def _bounded(value: str) -> str:
    return redact_sensitive_text(str(value or ""))[:MAX_EVIDENCE_CHARS]


def _repository_name(repository: str) -> str:
    value = repository.strip().removesuffix(".git")
    if value.startswith("git@github.com:"):
        value = value.split(":", 1)[1]
    elif "github.com/" in value:
        value = value.split("github.com/", 1)[1]
    value = value.strip("/")
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise MountedToolPreflightError(
            "GitHub capability requires an owner/repository target",
            code="github_repository_unauthorized",
            evidence={"phase": "authorization", "repository": _bounded(repository)},
        )
    return "/".join(parts)


def _trusted_gh_digest_checks() -> str:
    path = Path(__file__).resolve().parents[2] / "services/omnigent/tools/manifest.lock.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    tool = next(item for item in manifest["tools"] if item["name"] == "gh")
    digests = {item["sha256"] for item in tool["platforms"].values()}
    executable = f'/opt/moonmind-tools/{tool["path"]}'
    return " || ".join(
        f'''test "$(sha256sum {executable} | awk '{{print $1}}')" = "{digest}"'''
        for digest in sorted(digests)
    )


def _gh_probes(repository: str, *, mutation_required: bool) -> tuple[Probe, ...]:
    repo = _repository_name(repository)
    quoted_repo = shlex.quote(repo)
    probes = [
        Probe("manifest", _trusted_gh_digest_checks(), "tool_manifest_mismatch"),
        Probe("lookup", "command -v gh", "tool_not_visible_in_login_shell"),
        Probe("version", "gh --version", "tool_manifest_mismatch"),
        Probe("authentication", "gh auth status", "github_auth_unavailable"),
        Probe(
            "repository_access",
            f"gh repo view {quoted_repo} --json nameWithOwner,viewerPermission",
            "github_repository_unauthorized",
        ),
    ]
    if mutation_required:
        probes.append(
            Probe(
                "mutation_permission",
                f"test \"$(gh repo view {quoted_repo} --json viewerPermission --jq .viewerPermission)\" = ADMIN || "
                f"test \"$(gh repo view {quoted_repo} --json viewerPermission --jq .viewerPermission)\" = MAINTAIN || "
                f"test \"$(gh repo view {quoted_repo} --json viewerPermission --jq .viewerPermission)\" = WRITE",
                "github_repository_unauthorized",
            )
        )
    return tuple(probes)


async def preflight_mounted_tools(
    *,
    required_capabilities: Sequence[str],
    repository: str,
    mutation_required: bool,
    host_runner: CommandRunner,
    runner_runner: CommandRunner,
) -> dict[str, Any]:
    """Probe only declared tools through both real shell construction paths."""

    capabilities = {str(value).strip().lower() for value in required_capabilities}
    if "gh" not in capabilities:
        return {"status": "not_required", "boundaries": []}

    evidence: list[dict[str, str]] = []
    for boundary, command_runner in (("host", host_runner), ("runner", runner_runner)):
        for probe in _gh_probes(repository, mutation_required=mutation_required):
            rc, stdout, stderr = await command_runner(probe.command)
            item = {
                "boundary": boundary,
                "probe": probe.name,
                "status": "ready" if rc == 0 else "failed",
            }
            if stdout:
                item["output"] = _bounded(stdout)
            if rc != 0:
                if stderr:
                    item["error"] = _bounded(stderr)
                evidence.append(item)
                raise MountedToolPreflightError(
                    f"Mounted gh preflight failed during {boundary} {probe.name}",
                    code=probe.failure_code,
                    evidence={"tool": "gh", "phase": probe.name, "probes": evidence},
                )
            evidence.append(item)
    return {"status": "ready", "tool": "gh", "probes": evidence}


__all__ = ["MountedToolPreflightError", "preflight_mounted_tools"]
