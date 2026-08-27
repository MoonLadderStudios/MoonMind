"""Lease-owned GitHub CLI credential projection for generic Omnigent hosts."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from moonmind.auth.github_credentials import resolve_github_credential
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

_SAFE_VOLUME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_TARGET_PATH = "/home/app/.cache/moonmind-xdg"


def github_repository_from_request(request: AgentExecutionRequest) -> str:
    """Return the repository identity already admitted for the workspace."""

    spec = request.workspace_spec if isinstance(request.workspace_spec, dict) else {}
    target = (
        spec.get("repositoryTarget")
        if isinstance(spec.get("repositoryTarget"), dict)
        else {}
    )
    repository = (
        target.get("repository")
        if isinstance(target.get("repository"), dict)
        else {}
    )
    return str(
        repository.get("name")
        or spec.get("repository")
        or spec.get("repo")
        or ""
    ).strip()


class OmnigentGithubCredentialService:
    """Materialize standard ``gh`` config without exposing token transport."""

    def __init__(self, backend: DockerCommandBackend) -> None:
        self._backend = backend

    @staticmethod
    def required(resolved_tools: dict[str, Any]) -> bool:
        return "gh" in {
            str(value).strip().lower()
            for value in resolved_tools.get("tools", [])
            if str(value).strip()
        }

    @staticmethod
    def anticipated_attachment(
        resolved_tools: dict[str, Any], *, owner_ref: str
    ) -> dict[str, Any] | None:
        if not OmnigentGithubCredentialService.required(resolved_tools):
            return None
        owner_digest = hashlib.sha256(owner_ref.encode()).hexdigest()[:32]
        return {
            "kind": "volume",
            "sourceRef": f"mm-omnigent-github-{owner_digest}",
            "targetPath": _TARGET_PATH,
            "accessMode": "read-only",
            "cleanupRef": f"github-credential-cleanup:{owner_digest}",
            "ownerDigest": owner_digest,
        }

    async def materialize(
        self,
        *,
        request: AgentExecutionRequest,
        resolved_tools: dict[str, Any],
        owner_ref: str,
        writer_image_ref: str,
        runtime_uid: int,
        runtime_gid: int,
    ) -> dict[str, Any] | None:
        attachment = self.anticipated_attachment(resolved_tools, owner_ref=owner_ref)
        if attachment is None:
            return None
        repository = github_repository_from_request(request)
        credential = await resolve_github_credential(repo=repository or None)
        token = str(credential.token or "")
        if not token or "\n" in token or "\r" in token:
            raise HarnessPlatformError(
                credential.safe_summary,
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        if runtime_uid <= 0 or runtime_gid <= 0:
            raise HarnessPlatformError(
                "GitHub credential runtime owner is invalid",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        volume = str(attachment["sourceRef"])
        if not _SAFE_VOLUME.fullmatch(volume):
            raise HarnessPlatformError(
                "GitHub credential volume identity is unsafe",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        await self._backend.run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                "moonmind.owner=generic-omnigent-github-credential",
                "--label",
                f"moonmind.owner_digest={attachment['ownerDigest']}",
                volume,
            ],
            failure_code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
        _code, observed_owner, _error = await self._backend.run(
            [
                "docker",
                "volume",
                "inspect",
                "--format",
                '{{ index .Labels "moonmind.owner_digest" }}',
                volume,
            ],
            failure_code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
        if observed_owner.strip() != str(attachment["ownerDigest"]):
            raise HarnessPlatformError(
                "GitHub credential projection is owned by another lease",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        script = (
            "set -eu; umask 077; mkdir -p /config/gh; "
            "printf 'github.com:\\n    user: x-access-token\\n    oauth_token: ' "
            "> /config/gh/hosts.yml; "
            "cat >> /config/gh/hosts.yml; "
            "printf '\\n    git_protocol: https\\n' >> /config/gh/hosts.yml; "
            "chown -R \"$1:$2\" /config; "
            "chmod 0700 /config /config/gh; chmod 0600 /config/gh/hosts.yml"
        )
        try:
            await self._backend.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--network",
                    "none",
                    "--mount",
                    f"type=volume,src={volume},dst=/config",
                    "--entrypoint",
                    "/bin/sh",
                    writer_image_ref,
                    "-ceu",
                    script,
                    "--",
                    str(runtime_uid),
                    str(runtime_gid),
                ],
                input_bytes=token.encode(),
                failure_code=(
                    HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                ),
            )
        except BaseException:
            await self._backend.run(
                ["docker", "volume", "rm", volume], check=False
            )
            raise
        return attachment

    async def cleanup(self, attachment: dict[str, Any]) -> None:
        volume = str(attachment.get("sourceRef") or "")
        owner_digest = str(attachment.get("ownerDigest") or "")
        if not _SAFE_VOLUME.fullmatch(volume) or not owner_digest:
            raise HarnessPlatformError(
                "GitHub credential cleanup authority is unsafe",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        code, observed, error = await self._backend.run(
            [
                "docker",
                "volume",
                "inspect",
                "--format",
                '{{ index .Labels "moonmind.owner_digest" }}',
                volume,
            ],
            check=False,
        )
        if code != 0:
            if "no such" in error.lower():
                return
            raise HarnessPlatformError(
                "GitHub credential cleanup inspection is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        if observed.strip() != owner_digest:
            raise HarnessPlatformError(
                "stale owner cannot clean a newer GitHub credential projection",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        code, _out, _err = await self._backend.run(
            ["docker", "volume", "rm", volume], check=False
        )
        if code != 0:
            raise HarnessPlatformError(
                "GitHub credential cleanup is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )


__all__ = [
    "OmnigentGithubCredentialService",
    "github_repository_from_request",
]
