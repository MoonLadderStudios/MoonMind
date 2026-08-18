"""Deterministic production-interface harness for Checkpoint Branch turns.

The harness deliberately keeps :class:`OmnigentProfileBoundExecutionCoordinator`
as the lifecycle owner.  Only its external lease, host, bridge, provider,
publication, artifact, and cleanup interfaces are replaced, matching the
credential-free hermetic boundary required by MoonLadderStudios/MoonMind#3621.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from api_service.db.models import (
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentCheckpointIdentity,
    validate_branch_identity,
)
from moonmind.omnigent.policies import compile_policy_snapshot
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.provider_profiles.lease_client import CredentialLeasePurpose
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)

ArtifactWriter = Callable[[str, str, bytes], Awaitable[str]]


def checkpoint_branch_policy_snapshot() -> dict[str, Any]:
    """Return one validated on-demand policy for the real coordinator."""

    document = {
        "schemaVersion": 1,
        "endpoint": {"ref": "default", "bridgeModes": ["embedded"]},
        "execution": {
            "profileRef": "omnigent-codex@1",
            "harness": "codex-native",
            "agentIdentities": ["codex-native-ui"],
        },
        "host": {
            "mode": "on_demand_docker",
            "backendRef": "container-backend",
            "architectures": ["amd64"],
            "serverImageRef": "images/omnigent@sha256:" + "1" * 64,
            "hostImageRef": "images/host@sha256:" + "2" * 64,
        },
        "resources": {
            "cpuMillis": 2000,
            "memoryMiB": 4096,
            "processes": 256,
            "timeoutSeconds": 5400,
            "temporaryStorageMiB": 256,
            "concurrency": 1,
        },
        "network": {
            "attachmentRef": "control-plane-network",
            "egressProfileRef": "egress-default",
        },
        "workspace": {
            "allowedClasses": ["workflow"],
            "repositoryMutation": True,
            "mountClasses": ["workspace", "oauth_home"],
            "runtimeUid": 1000,
            "runtimeGid": 1000,
        },
        "providerProfile": {
            "compatibleProviders": ["codex"],
            "queueWhenBusy": True,
        },
        "session": {
            "create": True,
            "firstMessage": "required",
            "continuation": True,
            "interruption": True,
            "cancellation": True,
            "cleanup": "remove",
        },
        "capture": {
            "required": True,
            "artifactClasses": ["events", "snapshot"],
            "maxLogBytes": 1_000_000,
            "redaction": "required",
        },
        "checkpoint": {
            "capture": True,
            "resume": True,
            "branch": True,
            "publication": "approval",
            "promotion": "verified",
        },
        "remediation": {
            "actions": ["retry"],
            "riskTiers": {"retry": "low"},
            "locks": True,
            "maxActions": 3,
            "autonomous": False,
        },
        "rag": {
            "initialScope": "workflow",
            "followupScope": "session",
            "collectionRefs": ["default"],
            "tokenBudget": 4000,
            "fallback": "deny",
            "credentialRef": "retrieval-default",
        },
        "approvals": {
            "actions": {
                "read": {"decision": "allow", "reason": "read-only"},
                "publish": {
                    "decision": "approval_required",
                    "approvalClass": "release",
                    "reviewerRule": "owner",
                    "reason": "publication",
                },
            }
        },
        "retention": {"days": 30, "deletion": "after-expiry"},
        "rollout": {"cohort": "default", "gate": "ready", "diagnostics": True},
    }
    return compile_policy_snapshot(
        policy_id="codex-on-demand",
        version=1,
        document=document,
        validation={"valid": True, "diagnostics": []},
    )


class InjectedBoundaryFailure(RuntimeError):
    """One-shot deterministic failure at an external authority handoff."""


class CheckpointBranchRuntimeLedger:
    """Retry-stable identities and exact-once effects owned by boundary fakes."""

    def __init__(
        self,
        *,
        fail_stage: str | None = None,
        fail_position: str = "after",
        fail_always: bool = False,
        pause_stage: str | None = None,
        pause_position: str = "after",
        artifact_writer: ArtifactWriter | None = None,
    ) -> None:
        self.fail_stage = fail_stage
        self.fail_position = fail_position
        self.fail_always = fail_always
        self.pause_stage = pause_stage
        self.pause_position = pause_position
        self.failure_injected = False
        self.artifact_writer = artifact_writer
        self.boundary_reached = asyncio.Event()
        self._boundary_release = asyncio.Event()
        self.identities: dict[str, set[str]] = {
            name: set()
            for name in (
                "provider_lease",
                "host_lease",
                "host",
                "bridge_session",
                "provider_session",
                "first_message",
                "output",
                "branch",
                "commit",
                "pull_request",
                "publication",
                "cleanup",
                "capacity_release",
            )
        }
        self.effect_counts = dict.fromkeys(self.identities, 0)
        self.lifecycle: list[tuple[str, str | None]] = []
        self.workspace_locators: list[dict[str, Any]] = []
        self.requests: list[AgentExecutionRequest] = []
        self._host_leases: dict[str, OmnigentHostLease] = {}

    def own(self, kind: str, identity: str) -> str:
        owned = self.identities[kind]
        if identity not in owned:
            owned.add(identity)
            self.effect_counts[kind] += 1
        return identity

    def inject(self, stage: str, position: str) -> None:
        if (
            (self.fail_always or not self.failure_injected)
            and self.fail_stage == stage
            and self.fail_position == position
        ):
            self.failure_injected = True
            raise InjectedBoundaryFailure(f"{position} {stage}")

    async def boundary(self, stage: str, position: str) -> None:
        """Cross one external boundary, optionally pausing for real cancellation."""

        self.inject(stage, position)
        if self.pause_stage == stage and self.pause_position == position:
            self.boundary_reached.set()
            await self._boundary_release.wait()

    def release_boundary(self) -> None:
        """Unblock a paused boundary during test teardown."""

        self._boundary_release.set()

    async def artifact_ref(self, kind: str, key: str, body: bytes) -> str:
        if self.artifact_writer is not None:
            return await self.artifact_writer(kind, key, body)
        digest = hashlib.sha256(f"{key}:{kind}".encode()).hexdigest()
        return f"artifact://checkpoint-branch-runtime/{digest}/{kind}"


def _binding(key: str) -> OmnigentOAuthHostBinding:
    return OmnigentOAuthHostBinding(
        bindingRef=f"omnigent-oauth:profile-1:{key}",
        providerProfileId="profile-1",
        endpointRef="default",
        harness="codex-native",
        credentialMountRef=CredentialMountRef(
            authVolumeRef=AuthVolumeRef(
                providerProfileId="profile-1",
                runtimeId="codex_cli",
                providerId="openai",
                volumeRef="codex_auth_volume",
                credentialGeneration=1,
                ownerUserId="test-owner",
            ),
            targetPath="/home/app/.codex",
            runtimeUid=1000,
            runtimeGid=1000,
        ),
        hostLaunchProfileRef="codex-on-demand@1",
        executionProfileRef="omnigent-codex@1",
        launchPolicyRef="codex-on-demand@1",
    )


async def execute_checkpoint_branch_request(
    request: AgentExecutionRequest,
    *,
    ledger: CheckpointBranchRuntimeLedger,
    policy_snapshot: dict[str, Any] | None = None,
) -> AgentRunResult:
    """Drive a branch request through the real profile-bound coordinator."""

    recovery = request.checkpoint_recovery
    assert isinstance(recovery, dict)
    checkpoint = OmnigentCheckpointIdentity.model_validate(
        recovery["omnigentCheckpoint"]
    )
    candidate = CandidateWorkspaceAuthority(
        loopId=f"{checkpoint.workflow_id}:{checkpoint.logical_step_id}",
        attemptOrdinal=checkpoint.attempt_ordinal,
        headRef=checkpoint.head_ref,
        headDigest=checkpoint.head_digest,
        checkpointRef=checkpoint.workspace_checkpoint_ref,
        checkpointDigest=checkpoint.workspace_checkpoint_digest,
    )
    key = request.idempotency_key
    policy = policy_snapshot or checkpoint_branch_policy_snapshot()

    class LeaseClient:
        async def acquire_execution_lease(self, **_kwargs):
            await ledger.boundary("profile_lease", "before")
            lease_id = ledger.own("provider_lease", f"provider-lease:{key}")
            lease = SimpleNamespace(
                profile_id="profile-1",
                runtime_id="codex_cli",
                lease_id=lease_id,
                owner_id=f"owner:{key}",
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            )
            await ledger.boundary("profile_lease", "after")
            return lease

        async def release_lease(self, _lease) -> None:
            await ledger.boundary("capacity_release", "before")
            ledger.own("capacity_release", f"capacity-release:{key}")
            await ledger.boundary("capacity_release", "after")

        async def record_cooldown(self, **_kwargs) -> None:
            return None

    class Hosts:
        async def get_binding_for_profile(self, _profile_id):
            return _binding(key)

        async def create_or_update_static_binding(self, **kwargs):
            return _binding(key).model_copy(
                update={
                    "static_host_id": kwargs.get("static_host_id"),
                    "host_launch_profile_ref": kwargs.get(
                        "host_launch_profile_ref"
                    )
                    or "codex-on-demand@1",
                    "execution_profile_ref": kwargs.get("execution_profile_ref")
                    or "omnigent-codex@1",
                    "launch_policy_ref": kwargs.get("launch_policy_ref")
                    or "codex-on-demand@1",
                    "effective_launch_snapshot": kwargs.get(
                        "effective_launch_snapshot"
                    ),
                }
            )

        async def create_or_get_host_lease(self, **_kwargs):
            await ledger.boundary("host_lease", "before")
            lease_id = ledger.own("host_lease", f"host-lease:{key}")
            lease = ledger._host_leases.get(key)
            if lease is None:
                now = datetime.now(UTC)
                lease = OmnigentHostLease(
                    leaseId=lease_id,
                    providerProfileId="profile-1",
                    providerLeaseId=f"provider-lease:{key}",
                    bindingRef=f"omnigent-oauth:profile-1:{key}",
                    credentialGeneration=1,
                    status="allocating",
                    acquiredAt=now,
                    lastHeartbeatAt=now,
                    expiresAt=now + timedelta(hours=1),
                )
                ledger._host_leases[key] = lease
            await ledger.boundary("host_lease", "after")
            return lease

        async def restart_host_lease(self, _lease_id):
            lease = ledger._host_leases[key].model_copy(
                update={"status": "allocating", "omnigent_host_id": None}
            )
            ledger._host_leases[key] = lease
            return lease

        async def get_host_lease(self, _lease_id):
            return ledger._host_leases.get(key)

        async def claim_host_lease_cleanup(
            self,
            _lease_id,
            *,
            expected_status,
            expected_last_heartbeat_at,
            ttl_seconds,
        ):
            lease = ledger._host_leases[key]
            if (
                lease.status != expected_status
                or lease.last_heartbeat_at != expected_last_heartbeat_at
            ):
                return None
            now = datetime.now(UTC)
            lease = lease.model_copy(
                update={
                    "status": "draining",
                    "last_heartbeat_at": now,
                    "expires_at": now + timedelta(seconds=ttl_seconds),
                }
            )
            ledger._host_leases[key] = lease
            return lease

        async def transition_host_lease(
            self, _lease_id, *, expected_status, new_status, fields=None
        ):
            lease = ledger._host_leases[key]
            assert lease.status == expected_status
            lease = lease.model_copy(
                update={"status": new_status, **dict(fields or {})}
            )
            ledger._host_leases[key] = lease
            return lease

        async def heartbeat_host_lease(self, _lease_id, *, ttl_seconds):
            assert ttl_seconds > 0
            return ledger._host_leases[key]

        async def mark_host_lease_stopped(self, _lease_id):
            lease = ledger._host_leases[key].model_copy(update={"status": "stopped"})
            ledger._host_leases[key] = lease
            return lease

        async def mark_host_lease_failed(self, _lease_id, **_kwargs):
            lease = ledger._host_leases[key].model_copy(update={"status": "failed"})
            ledger._host_leases[key] = lease
            return lease

    class Runtime:
        async def prepare_host(self, **kwargs):
            await ledger.boundary("host_start", "before")
            host_id = ledger.own("host", f"host:{key}")
            ledger.workspace_locators.append(dict(kwargs["workspace_locator"]))
            await ledger.boundary("host_start", "after")
            return {
                "hostId": host_id,
                "workspacePath": f"/workspaces/{hashlib.sha256(key.encode()).hexdigest()[:16]}",
                "workspaceResolution": {
                    "locatorKind": "sandbox",
                    "identityVerified": True,
                    "materializationAction": "restored",
                },
            }

        async def inspect_session_completion(self, _session_id):
            return {
                "sessionStatus": "completed",
                "itemCount": 3,
                "assistantMessageCount": 1,
                "toolResultCount": 1,
                "terminalAssistantAfterWork": True,
            }

        async def publish_workspace(self, **kwargs):
            await ledger.boundary("publication", "before")
            branch = ledger.own(
                "branch", str(kwargs.get("publication_identity") or key)
            )
            commit = ledger.own("commit", f"commit:{key}")
            pull_request = None
            if str(kwargs.get("publish_mode") or "") in {"pr", "pull_request"}:
                pull_request = ledger.own(
                    "pull_request",
                    "https://example.test/pull/"
                    + hashlib.sha256(key.encode()).hexdigest()[:8],
                )
            ledger.own("publication", f"publication:{key}")
            await ledger.boundary("publication", "after")
            return {
                "push_status": "pushed",
                "push_branch": branch,
                "push_base_branch": "main",
                "push_head_sha": hashlib.sha1(commit.encode()).hexdigest(),
                "push_commit_count": 1,
                **(
                    {"pull_request_url": pull_request}
                    if pull_request is not None
                    else {}
                ),
                "remote_verified": True,
            }

        async def stop_host(self, **_kwargs):
            await ledger.boundary("cleanup", "before")
            ledger.own("cleanup", f"cleanup:{key}")
            await ledger.boundary("cleanup", "after")

    class Store:
        async def get_or_create(self, **_kwargs):
            return SimpleNamespace(bridge_session_id=f"bridge:{key}")

        async def bind_profile_authorization(self, **_kwargs):
            bridge_id = ledger.own("bridge_session", f"bridge:{key}")
            return SimpleNamespace(bridge_session_id=bridge_id)

        async def record_lifecycle_event(
            self, _key, *, event_type, status=None, **_kwargs
        ) -> None:
            ledger.lifecycle.append((event_type, status))

        async def mark_terminal(self, *_args, **_kwargs) -> None:
            return None

    async def execute_provider(bound_request, **_kwargs):
        ledger.requests.append(bound_request)
        await ledger.boundary("session_creation", "before")
        provider_session_id = ledger.own(
            "provider_session", f"provider-session:{key}"
        )
        await ledger.boundary("session_creation", "after")
        await ledger.boundary("first_message", "before")
        first_message_id = ledger.own("first_message", f"first-message:{key}")
        await ledger.boundary("first_message", "after")
        await ledger.boundary("terminal_harvest", "before")
        output_ref = await ledger.artifact_ref(
            "output", key, f"completed:{key}".encode()
        )
        diagnostics_ref = await ledger.artifact_ref(
            "diagnostics", key, b'{"status":"completed"}'
        )
        external_state_ref = await ledger.artifact_ref(
            "external-state", key, f"state:{key}".encode()
        )
        ledger.own("output", output_ref)
        await ledger.boundary("terminal_harvest", "after")
        validate_branch_identity(
            checkpoint,
            new_host_lease_ref=f"host-lease:{key}",
            new_session_id=provider_session_id,
        )
        return AgentRunResult(
            outputRefs=[output_ref],
            summary="deterministic profile-bound branch turn completed",
            diagnosticsRef=diagnostics_ref,
            metadata={
                "omnigentSessionId": provider_session_id,
                "externalStateRef": external_state_ref,
                "firstMessageId": first_message_id,
                "authorityChain": {
                    "schemaVersion": "omnigent-authority-chain-v1",
                    "workspace": {
                        "locatorKind": "sandbox",
                        "identityVerified": True,
                    },
                    "publication": {
                        "publishMode": str(
                            (request.parameters or {}).get("publishMode") or "none"
                        ),
                        "declaredOutputRefs": [output_ref],
                    },
                    "terminal": {
                        "harvestState": "completed",
                        "cleanupCompleted": True,
                        "leaseReleased": True,
                        "janitorRequired": False,
                        "releaseOrdering": [
                            "host_cleanup_completed",
                            "provider_lease_released",
                            "terminal",
                        ],
                    },
                },
            },
        )

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=LeaseClient(),
        host_repository=Hosts(),
        host_runtime=Runtime(),
        run_store=Store(),
        execution_runner=execute_provider,
        artifact_gateway=object(),
    )

    async def resolve_profile(_profile_id: str):
        return SimpleNamespace(
            enabled=True,
            auth_state=ProviderProfileAuthState.CONNECTED,
            disabled_reason=None,
            max_parallel_runs=1,
            cooldown_after_429_seconds=900,
            runtime_id="codex_cli",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
            volume_ref="codex_auth_volume",
            volume_mount_path="/home/app/.codex",
            secret_refs={},
            command_behavior={},
        )

    async def resolve_policy(_policy_ref: str) -> dict[str, Any]:
        return policy

    coordinator._resolve_profile = resolve_profile  # type: ignore[method-assign]
    coordinator._resolve_policy_snapshot = resolve_policy  # type: ignore[method-assign]
    return await coordinator.branch_from_checkpoint(
        request=request,
        checkpoint=checkpoint,
        current_credential_generation=checkpoint.credential_generation,
        candidate_workspace=candidate,
    )


__all__ = [
    "CheckpointBranchRuntimeLedger",
    "InjectedBoundaryFailure",
    "checkpoint_branch_policy_snapshot",
    "execute_checkpoint_branch_request",
]
