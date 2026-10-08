"""Connection-bound GitHub acquisition and attempt-owned CLI projection."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlsplit

from moonmind.auth.bound_acquisition import AcquiredCredential, SelectionSnapshot
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.executions.repository_contract import (
    RepositoryIdentity,
    github_repository_name_from_value,
    normalize_endpoint,
)

_SAFE_VOLUME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_TARGET_PATH = "/run/mm-credentials/github"
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def github_host_from_endpoint(endpoint: str) -> str:
    """Return the Git/CLI authority after deployment-owned endpoint validation."""
    from moonmind.auth.github_app_wiring import github_api_base_for

    api_base = github_api_base_for(endpoint)
    if api_base == "https://api.github.com":
        return "github.com"
    return urlsplit(normalize_endpoint(endpoint)).netloc


def normalize_github_repository_remote(
    repository: str, *, endpoint: str = "https://github.com"
) -> str | None:
    """Normalize a clean repository target on its already selected GitHub host."""
    try:
        host = github_host_from_endpoint(endpoint)
        cleaned = str(repository or "").strip().rstrip("/")
        if _REPOSITORY_NAME.fullmatch(cleaned):
            return f"https://{host}/{cleaned.removesuffix('.git')}.git"
        parsed = urlsplit(cleaned)
        name = parsed.path.removeprefix("/").removesuffix(".git")
        if (
            parsed.scheme.lower() != "https"
            or normalize_endpoint(f"{parsed.scheme}://{parsed.netloc}")
            != f"https://{host}"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not _REPOSITORY_NAME.fullmatch(name)
        ):
            return None
        return f"https://{host}/{name}.git"
    except ValueError:
        return None


def github_clone_source_from_identity(identity: RepositoryIdentity) -> str:
    """Use the admitted canonical remote, deriving only when it carries an ID."""
    # Validate trust independently of normalization, so acquisition reports
    # unsafe deployment destinations without ever resolving a secret.
    github_host_from_endpoint(identity.endpoint)
    remote = normalize_github_repository_remote(
        identity.canonical_remote or identity.display_name,
        endpoint=identity.endpoint,
    )
    if remote is None:
        raise ValueError("admitted GitHub repository canonical remote is unsafe")
    return remote


def github_repository_from_request(request: AgentExecutionRequest) -> str:
    """Return the repository identity already admitted for the workspace."""

    from moonmind.omnigent.workspace_sources import compile_workspace_source

    source = compile_workspace_source(
        request.workspace_spec or {},
        workflow_id=(
            request.step_execution.workflow_id
            if request.step_execution
            else request.correlation_id
        ),
        step_execution_id=(
            request.step_execution.step_execution_id
            if request.step_execution
            else request.idempotency_key
        ),
        runtime="omnigent",
    )
    return source.repository_ref or ""


class OmnigentGithubCredentialService:
    """Materialize standard ``gh`` config without exposing token transport."""

    def __init__(
        self,
        backend: DockerCommandBackend | None,
        *,
        session_factory: Any | None = None,
        artifact_gateway: Any | None = None,
    ) -> None:
        self._backend = backend
        self._sessions = session_factory
        self._artifacts = artifact_gateway

    async def _verified_repository_access(
        self,
        *,
        plan: OmnigentExecutionPlanEnvelope,
        request: AgentExecutionRequest | None,
        role: str,
        operation: str,
        repository: str | None,
        consumer: Literal["agent", "native", "server"] = "agent",
    ) -> tuple[
        str, dict[str, Any], dict[str, Any], SelectionSnapshot, RepositoryIdentity
    ]:
        slot = {
            "source_read": "source",
            "collaboration": "collaboration",
            "destination_write": "destination",
        }.get(role)
        if consumer not in {"agent", "native", "server"}:
            raise ValueError("repository consumer is unsupported")
        if consumer == "native" and (
            request is not None
            or role != "collaboration"
            or operation not in {"read", "review_request"}
        ):
            raise ValueError(
                "native repository consumer only admits trusted review operations"
            )
        if consumer == "server" and (
            request is not None
            or role not in {"collaboration", "source_read"}
            or operation not in {"read", "review_request", "merge_request"}
            or (role == "source_read" and operation != "read")
        ):
            raise ValueError(
                "server repository consumer requires a trusted repository operation"
            )
        if request is None and consumer not in {"native", "server"}:
            raise ValueError("native repository use requires an explicit consumer")
        binding = (
            plan.payload.credentialBindings.get(slot) if plan is not None else None
        )
        if (
            binding is not None
            and getattr(binding, "consumer", "agent") == "native"
            and consumer != "native"
        ):
            raise ValueError(
                "native repository authority cannot be consumed by an agent"
            )
        access = (
            plan.payload.resolvedTools.get("repositoryAccess", {}).get(slot)
            if plan is not None
            else None
        )
        if not access or self._artifacts is None:
            raise ValueError(
                "repository operation requires its admitted access snapshot"
            )
        # Native plan consumers use the gateway's explicit principal ACL;
        # agent consumers retain their linked Step Execution authority.
        body = (
            await self._artifacts.read_repository_access_snapshot(
                access["artifactRef"], request=request
            )
            if request is not None
            else await self._artifacts.read_bytes(access["artifactRef"])
        )
        if (
            "repository-access-snapshot:sha256:" + hashlib.sha256(body).hexdigest()
            != access["snapshotRef"]
        ):
            raise ValueError("repository access snapshot digest mismatch")
        payload = json.loads(body)
        snapshot = SelectionSnapshot.model_validate(payload["selection"])
        identity = RepositoryIdentity.model_validate(payload["repositoryIdentity"])
        if (
            binding is not None
            and getattr(binding, "consumer", "agent") == "native"
            and not set(snapshot.operations).issubset({"read", "review_request"})
        ):
            raise ValueError("native repository authority has unsupported operations")
        if (
            normalize_endpoint(identity.endpoint)
            != normalize_endpoint(snapshot.endpoint)
            or identity.route_id() != snapshot.route_id
            or identity.display_name.lower() != snapshot.repository_display.lower()
        ):
            raise ValueError("repository access snapshot identity conflicts")
        clone_source = github_clone_source_from_identity(identity)
        # A durable schedule plan can serve several fresh execution owners.
        # Bind to the admitted plan rather than its authoring subject.
        requested_plan_ref = (
            request.parameters.get("executionPlanRef") if request is not None else None
        )
        if (
            request is not None
            and request.step_execution
            and request.step_execution.omnigent_execution_plan
        ):
            requested_plan_ref = request.step_execution.omnigent_execution_plan.plan_ref
        if requested_plan_ref and requested_plan_ref != plan.planRef:
            raise ValueError(
                "repository consumer conflicts with admitted execution plan"
            )
        requested_repository = repository or (
            github_repository_from_request(request) if request is not None else ""
        )
        direct_name = str(requested_repository or "").strip().rstrip("/")
        direct_name = direct_name.removesuffix(".git")
        if _REPOSITORY_NAME.fullmatch(direct_name):
            target_matches = direct_name.lower() == snapshot.repository_display.lower()
        elif str(requested_repository).startswith("git@github.com:"):
            target_matches = (
                github_host_from_endpoint(identity.endpoint) == "github.com"
                and github_repository_name_from_value(requested_repository).lower()
                == snapshot.repository_display.lower()
            )
        else:
            requested_remote = normalize_github_repository_remote(
                requested_repository, endpoint=identity.endpoint
            )
            target_matches = bool(
                requested_remote and requested_remote.lower() == clone_source.lower()
            )
        if not target_matches:
            raise ValueError(
                "repository consumer target conflicts with admitted snapshot"
            )
        if snapshot.role != role or operation not in snapshot.operations:
            raise ValueError("repository operation or role is not admitted")
        return slot, access, payload, snapshot, identity

    async def admitted_repository_identity(
        self,
        *,
        plan: OmnigentExecutionPlanEnvelope,
        request: AgentExecutionRequest | None,
        role: str,
        operation: str,
        repository: str | None = None,
        consumer: Literal["agent", "native", "server"] = "agent",
    ) -> RepositoryIdentity:
        """Resolve the snapshot's target without acquiring or exposing a token."""
        _slot, _access, _payload, _snapshot, identity = (
            await self._verified_repository_access(
                plan=plan,
                request=request,
                role=role,
                operation=operation,
                repository=repository,
                consumer=consumer,
            )
        )
        return identity

    async def acquire_repository_use(
        self,
        *,
        plan: OmnigentExecutionPlanEnvelope,
        request: AgentExecutionRequest | None,
        role: str,
        operation: str,
        repository: str | None = None,
        execution_owner: str | None = None,
        authority_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        consumer: Literal["agent", "native", "server"] = "agent",
    ) -> AcquiredCredential | None:
        """Consume the compiler's immutable selection, never current defaults."""
        from api_service.services.repository_connections import (
            RepositoryConnectionService,
        )
        from moonmind.auth.bound_acquisition import (
            AccessMode,
            AcquisitionRequest,
            select_repository_authority,
        )
        from moonmind.auth.github_app_wiring import (
            build_bound_acquirer_for_connection,
            revision_reader_for,
        )

        if request is None and (not repository or not execution_owner):
            raise ValueError(
                "native repository use requires an explicit target and owner"
            )
        use_owner = execution_owner or request.idempotency_key
        slot, access, payload, snapshot, identity = (
            await self._verified_repository_access(
                plan=plan,
                request=request,
                role=role,
                operation=operation,
                repository=repository,
                consumer=consumer,
            )
        )
        binding = plan.payload.credentialBindings.get(slot)
        if snapshot.access_mode == AccessMode.ANONYMOUS:
            if role != "source_read" or operation != "read" or binding is not None:
                raise ValueError("anonymous access cannot carry credential authority")
            return None
        if (
            binding is None
            or binding.repositoryRole != role
            or binding.connectionRef != snapshot.connection_id
            or binding.repositoryAccessSnapshotRef != access["snapshotRef"]
        ):
            raise ValueError(
                "repository consumer has undeclared or wrong-role authority"
            )
        if self._sessions is None:
            raise ValueError("repository connection reader is unavailable")

        async def current_connection():
            async with self._sessions() as session:
                connections = RepositoryConnectionService(session)
                connection = await connections.get_connection(
                    snapshot.connection_id,
                    principal_ref=snapshot.principal_ref,
                    principal_scope=(snapshot.scope_type, snapshot.scope_ref),
                )
                if connection is None:
                    raise ValueError("selected repository connection is unavailable")
                assignment = await connections.launch_assignment(
                    connection, snapshot.repository_display
                )
                if (
                    assignment.identity.route_id() != snapshot.route_id
                    or assignment.revision != payload["assignmentRevision"]
                ):
                    raise ValueError(
                        "repository assignment changed; re-admit the attempt"
                    )
                select_repository_authority(
                    access_mode=AccessMode.EXPLICIT,
                    principal_ref=snapshot.principal_ref,
                    principal_scope=(snapshot.scope_type, snapshot.scope_ref),
                    identity=identity,
                    role=role,
                    requested_operations=snapshot.operations,
                    policy_revision=connection.policy_revision,
                    explicit_connection=connection,
                    explicit_assignment=assignment,
                )
                return connection

        connection = await current_connection()

        async def revision_reader(connection_id):
            current = await current_connection()
            return await revision_reader_for({connection_id: current})(connection_id)

        from moonmind.config.settings import settings

        acquirer = build_bound_acquirer_for_connection(
            connection,
            revision_reader=revision_reader,
            target_repository=snapshot.repository_display,
            permitted_repositories=(snapshot.repository_display,),
            allowed_api_hosts=tuple(
                (settings.github.github_trusted_api_hosts or "").split(",")
            ),
        )
        acquired = await acquirer.acquire(
            AcquisitionRequest(
                snapshot=snapshot,
                execution_owner=use_owner,
                operation_id=f"{use_owner}:{slot}:{operation}",
            )
        )
        try:
            if authority_sink is not None:
                from moonmind.omnigent.harness_platform.runtime_binding import (
                    RepositoryIssuanceRecord,
                )

                record = RepositoryIssuanceRecord(
                    connectionRef=acquired.binding.connection_id,
                    issuanceRef=acquired.binding.issuance_id,
                    snapshotRef=access["snapshotRef"],
                    credentialRevision=str(acquired.binding.credential_revision),
                    useOwner=acquired.binding.execution_owner,
                )
                await authority_sink(
                    {
                        "kind": "repository_use",
                        "slot": slot,
                        "generation": acquired.binding.generation,
                        "record": record.model_dump(by_alias=True, mode="json"),
                    }
                )
        except BaseException:
            acquired.credential.clear()
            raise
        return acquired

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
        if not OmnigentGithubCredentialService.required(
            resolved_tools
        ) or not resolved_tools.get("repositoryAccess", {}).get("collaboration"):
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

    async def anticipated_attachment_for_request(
        self,
        resolved_tools: dict[str, Any],
        *,
        plan: OmnigentExecutionPlanEnvelope,
        request: AgentExecutionRequest,
        owner_ref: str,
    ) -> dict[str, Any] | None:
        """Bind non-secret host metadata before recording projection authority."""
        attachment = self.anticipated_attachment(resolved_tools, owner_ref=owner_ref)
        if attachment is None:
            return None
        identity = await self.admitted_repository_identity(
            plan=plan, request=request, role="collaboration", operation="read"
        )
        return {
            **attachment,
            "githubHost": github_host_from_endpoint(identity.endpoint),
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
        expected_omnigent_version: str = "",
        plan: OmnigentExecutionPlanEnvelope | None = None,
        authority_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any] | None:
        attachment = self.anticipated_attachment(resolved_tools, owner_ref=owner_ref)
        if attachment is None:
            return None
        acquired = await self.acquire_repository_use(
            plan=plan,
            request=request,
            role="collaboration",
            operation="read",
            execution_owner=owner_ref,
            authority_sink=authority_sink,
        )
        try:
            attachment = {
                **attachment,
                "githubHost": github_host_from_endpoint(acquired.binding.endpoint),
            }
            return await self._materialize_acquired(
                attachment=attachment,
                acquired=acquired,
                writer_image_ref=writer_image_ref,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                expected_omnigent_version=expected_omnigent_version,
            )
        finally:
            acquired.credential.clear()

    async def _materialize_acquired(
        self,
        *,
        attachment,
        acquired,
        writer_image_ref,
        runtime_uid,
        runtime_gid,
        expected_omnigent_version,
    ):
        token = acquired.credential.use_now(
            lambda material: bytes(material).decode("utf-8")
        )
        if not token or "\n" in token or "\r" in token:
            raise HarnessPlatformError(
                "admitted repository issuance returned invalid credential material",
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
            "set -eu; umask 077; mkdir -p /config; "
            "printf '%s:\\n    user: x-access-token\\n    oauth_token: ' \"$3\" "
            "> /config/hosts.yml; "
            "cat >> /config/hosts.yml; "
            "printf '\\n    git_protocol: https\\n' >> /config/hosts.yml; "
            'chown -R "$1:$2" /config; '
            "chmod 0700 /config; chmod 0600 /config/hosts.yml"
        )
        # Same-repo SHA drift recovery as credential writers: reuse a qualified
        # deployment image when the plan-pinned digest is absent, avoiding a
        # 7GB exact pull for patch rebuilds. Qualification (digest pin plus
        # observed or operator-pinned authority plus admitted series) happens
        # before the token reaches the fallback image.
        effective_writer = str(writer_image_ref or "").strip()
        if "@sha256:" in effective_writer:
            try:
                from moonmind.omnigent.host_image_drift import (
                    compatible_deployed_fallback,
                )

                fallback = compatible_deployed_fallback(
                    effective_writer,
                    expected_omnigent_version=expected_omnigent_version,
                )
            except Exception:
                fallback = None
            if fallback is not None:
                try:
                    fcode, _, _ = await self._backend.run(
                        [
                            "docker",
                            "image",
                            "inspect",
                            fallback,
                            "--format",
                            "{{.Id}}",
                        ],
                        check=False,
                    )
                except Exception:
                    fcode = 1
                if fcode == 0:
                    try:
                        rcode, _, _ = await self._backend.run(
                            [
                                "docker",
                                "image",
                                "inspect",
                                effective_writer,
                                "--format",
                                "{{.Id}}",
                            ],
                            check=False,
                        )
                    except Exception:
                        rcode = 1
                    if rcode != 0:
                        effective_writer = fallback
        try:
            await self._backend.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    # The selected host image runs workloads as the requested
                    # runtime UID.  This isolated, networkless setup process
                    # needs root only to initialize and hand off the credential
                    # volume to that UID.
                    "--user",
                    "0:0",
                    "--network",
                    "none",
                    "--mount",
                    f"type=volume,src={volume},dst=/config",
                    "--entrypoint",
                    "/bin/sh",
                    effective_writer,
                    "-ceu",
                    script,
                    "--",
                    str(runtime_uid),
                    str(runtime_gid),
                    str(attachment["githubHost"]),
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
    "github_clone_source_from_identity",
    "github_host_from_endpoint",
    "github_repository_from_request",
    "normalize_github_repository_remote",
]
