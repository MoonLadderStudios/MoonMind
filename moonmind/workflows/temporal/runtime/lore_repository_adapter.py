"""Portable, machine-readable Lore repository readiness adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.executions.repository_contract import (
    AuthoredLoreRepositoryTarget,
    CapabilityReadinessRegistry,
    RepositoryClientEvidence,
    RepositoryConnection,
    RepositoryContractError,
    ResolvedRepositoryTarget,
    derive_repository_capabilities,
    load_repository_connection,
    materialize_resolved_repository_target,
    validate_connection_and_client,
)


class _LoreResolvedIdentity(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    repository_id: str = Field(alias="repositoryId", min_length=1)
    repository_name: str = Field(alias="repositoryName", min_length=1)
    branch_id: str = Field(alias="branchId", min_length=1)
    branch_name: str = Field(alias="branchName", min_length=1)
    revision_signature: str = Field(alias="revisionSignature", min_length=1)
    server_version: str = Field(alias="serverVersion", min_length=1)


class LoreCliReadinessAdapter:
    """Resolve Lore authority through a pinned CLI that emits strict JSON."""

    def __init__(
        self,
        *,
        connections_dir: Path,
        executable: str | None,
        tool_bundle_ref: str | None,
        allow_trusted_network_development: bool = False,
    ) -> None:
        self._connections_dir = connections_dir
        self._executable = executable
        self._tool_bundle_ref = tool_bundle_ref
        self._allow_trusted_network_development = allow_trusted_network_development

    @classmethod
    def from_environment(cls) -> "LoreCliReadinessAdapter":
        runtime_root = Path(
            os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs")
        )
        return cls(
            connections_dir=Path(
                os.environ.get(
                    "MOONMIND_REPOSITORY_CONNECTIONS_DIR",
                    str(runtime_root / "repository_connections"),
                )
            ),
            executable=os.environ.get("MOONMIND_LORE_CLIENT_EXECUTABLE"),
            tool_bundle_ref=os.environ.get("MOONMIND_LORE_TOOL_BUNDLE_REF"),
            allow_trusted_network_development=os.environ.get(
                "MOONMIND_LORE_ALLOW_TRUSTED_NETWORK_DEVELOPMENT", ""
            ).strip().lower()
            in {"1", "true", "yes"},
        )

    def _load_connection(self, connection_ref: str) -> RepositoryConnection:
        try:
            candidates = sorted(self._connections_dir.glob("*.json"))
        except OSError as exc:
            raise RepositoryContractError(
                "LORE_CONNECTION_NOT_READY", "Lore connection registry is unavailable"
            ) from exc
        for path in candidates:
            try:
                connection = load_repository_connection(path, connection_ref)
            except RepositoryContractError:
                continue
            if connection.provider == "lore":
                return connection
        raise RepositoryContractError(
            "LORE_CONNECTION_NOT_READY",
            f"deployment-owned Lore connection {connection_ref!r} is unavailable",
        )

    def _resolve_executable(self) -> Path:
        candidate = self._executable or shutil.which("lore")
        if not candidate:
            raise RepositoryContractError(
                "LORE_CLIENT_UNAVAILABLE", "no Lore client executable is configured"
            )
        path = Path(candidate).resolve()
        if not path.is_file():
            raise RepositoryContractError(
                "LORE_CLIENT_UNAVAILABLE", "configured Lore client does not exist"
            )
        return path

    async def _run(self, executable: Path, *args: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            str(executable),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return (
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    async def __call__(
        self, target: AuthoredLoreRepositoryTarget, request: AgentExecutionRequest
    ) -> ResolvedRepositoryTarget:
        connection = self._load_connection(target.connection_ref)
        if (
            connection.credential.source == "trusted_network_development"
            and not self._allow_trusted_network_development
        ):
            raise RepositoryContractError(
                "LORE_CONNECTION_NOT_READY",
                "trusted-network Lore credentials require explicit development policy",
            )
        executable = self._resolve_executable()
        if not self._tool_bundle_ref:
            raise RepositoryContractError(
                "LORE_CLIENT_UNAVAILABLE", "Lore tool-bundle identity is not configured"
            )
        version_code, version_stdout, _version_stderr = await self._run(
            executable, "--version"
        )
        if version_code != 0 or not version_stdout.strip():
            raise RepositoryContractError(
                "LORE_CLIENT_UNAVAILABLE",
                "Lore client version probe failed",
            )
        client_version = version_stdout.strip().removeprefix("lore ").strip()
        arguments = [
            "repository",
            "resolve",
            "--endpoint-ref",
            connection.endpoint_ref,
            "--repository",
            target.repository.name,
            "--branch",
            target.branch.name,
            "--output",
            "json",
        ]
        if connection.trust_bundle_ref is not None:
            arguments.extend(["--trust-bundle-ref", connection.trust_bundle_ref])
        if connection.credential.source == "secret_ref":
            arguments.extend(
                [
                    "--credential-ref-json",
                    json.dumps(
                        connection.credential.credential_ref.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ]
            )
        else:
            arguments.append("--trusted-network-development")
        if target.revision is not None:
            arguments.extend(
                ["--revision-signature", target.revision.revision_signature]
            )
        code, stdout, _stderr = await self._run(executable, *arguments)
        if code != 0:
            raise RepositoryContractError(
                "LORE_CONNECTION_NOT_READY",
                "Lore target resolution failed",
            )
        try:
            identity = _LoreResolvedIdentity.model_validate_json(stdout)
        except ValueError as exc:
            raise RepositoryContractError(
                "LORE_CONNECTION_NOT_READY",
                "Lore client returned invalid machine-readable authority evidence",
            ) from exc
        if (
            identity.repository_name != target.repository.name
            or identity.branch_name != target.branch.name
            or (
                target.revision is not None
                and identity.revision_signature
                != target.revision.revision_signature
            )
        ):
            raise RepositoryContractError(
                "LORE_CONNECTION_NOT_READY",
                "Lore client authority does not match the authored target",
            )
        if (
            connection.allowed_repository_ids
            and identity.repository_id not in connection.allowed_repository_ids
        ):
            raise RepositoryContractError(
                "REPOSITORY_CONNECTION_MISMATCH",
                "resolved Lore repository is not allowed by connection",
            )
        evidence = RepositoryClientEvidence(
            toolBundleRef=self._tool_bundle_ref,
            clientVersion=client_version,
            executableSha256=f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}",
            serverVersion=identity.server_version,
        )
        parameters: Mapping[str, Any] = (
            request.parameters if isinstance(request.parameters, Mapping) else {}
        )
        publish_mode = str(parameters.get("publishMode") or "none").lower()
        operation = "write" if publish_mode in {"branch", "pr"} else "read"
        validate_connection_and_client(
            target, connection, evidence, operation=operation
        )
        registry = CapabilityReadinessRegistry(
            runtime_owned_tokens=(
                "artifact.read",
                "artifact.write",
                "jira",
                "docker",
                "container.run",
            )
        )
        registry.register("lore", lambda _context: True)
        operation_tokens = {
            "repo.read": "read",
            "repo.write": "write",
            "repo.branch.write": "branch_write",
            "repo.lock": "lock",
            "repo.review.request": "review_request",
        }
        for token, allowed_operation in operation_tokens.items():
            registry.register(
                token,
                lambda _context, required=allowed_operation: required
                in connection.allowed_operations,
            )
        skill_caps = (
            request.skill.get("requiredCapabilities", ())
            if isinstance(request.skill, Mapping)
            else ()
        )
        tool_caps = parameters.get("repositoryToolCapabilities", ())
        if not isinstance(skill_caps, (list, tuple)):
            skill_caps = ()
        if not isinstance(tool_caps, (list, tuple)):
            tool_caps = ()
        required = derive_repository_capabilities(
            target,
            publish_mode=publish_mode,
            skill_capabilities=skill_caps,
            tool_capabilities=tool_caps,
        )
        await registry.check(required, {"connection": connection, "target": target})
        return materialize_resolved_repository_target(
            target,
            observed_revision=identity.revision_signature,
            evidence=evidence,
            client_policy=connection.client_policy,
            publish_mode=publish_mode,
            repository_id=identity.repository_id,
            branch_id=identity.branch_id,
            projection=connection.projection,
        )


__all__ = ["LoreCliReadinessAdapter"]
