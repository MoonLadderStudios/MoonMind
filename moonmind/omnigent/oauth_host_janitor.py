"""Reconcile stale profile-bound Omnigent OAuth hosts and durable leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from moonmind.omnigent.host_ports import OmnigentHostReclamationPorts
from moonmind.omnigent.oauth_host_runtime import (
    OmnigentEgressEvidenceRequestIdentity,
)
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.oauth_hosts import (
    CLEANUP_CLAIMABLE_HOST_STATES,
    OmnigentOAuthHostRepository,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient


class OmnigentOAuthHostJanitor:
    def __init__(
        self,
        *,
        repository: OmnigentOAuthHostRepository,
        runtime: OmnigentHostReclamationPorts,
        client: OmnigentHttpClient,
        run_store: Any | None = None,
        lease_client: ProviderProfileLeaseClient | None = None,
        artifact_gateway: Any | None = None,
        runtime_binding_store: Any | None = None,
        control_plane_store: Any | None = None,
        heartbeat_timeout_seconds: int = 90,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._client = client
        self._run_store = run_store
        self._lease_client = lease_client
        self._artifact_gateway = artifact_gateway
        self._runtime_binding_store = runtime_binding_store
        self._control_plane_store = control_plane_store
        self._heartbeat_timeout = timedelta(
            seconds=max(30, heartbeat_timeout_seconds)
        )

    async def _runtime_binding_cleanup_authority(
        self, *, binding: Any, lease: Any
    ) -> Any | None:
        """Load the current fenced binding before plan-bound cleanup."""

        if self._runtime_binding_store is None:
            return None
        state = await self._runtime_binding_store.get_state_for_host_lease(
            lease.lease_id
        )
        if state is None:
            execution_scope_ref = str(
                getattr(lease, "holder_workflow_id", None) or ""
            ).strip()
            if execution_scope_ref and self._control_plane_store is not None:
                async with self._control_plane_store.transaction() as repositories:
                    session = await repositories.sessions.get(
                        execution_scope_ref
                    )
                if session is not None:
                    execution_scope_ref = str(
                        session.moonmind_workflow_id or ""
                    ).strip()
            current = (
                await self._runtime_binding_store.get_current_state_for_execution_scope(
                    execution_scope_ref
                )
                if execution_scope_ref
                else None
            )
            if current is not None:
                raise ValueError(
                    "janitor host lease is fenced by replacement runtime authority"
                )
            # Historical host leases predate execution-plan bindings and retain
            # their legacy cleanup authority.
            return None
        runtime = state.binding
        if (
            runtime.hostBindingRef != binding.binding_ref
            or runtime.hostLeaseRef != lease.lease_id
            or runtime.omnigentHostId != lease.omnigent_host_id
            or not any(
                acquired.providerProfileRef == lease.provider_profile_id
                and acquired.credentialGeneration
                == lease.credential_generation
                for acquired in runtime.providerLeases.values()
            )
        ):
            raise ValueError(
                "janitor host authority conflicts with current runtime binding"
            )
        return state

    async def _complete_runtime_binding_cleanup(self, state: Any | None) -> None:
        if state is None or self._runtime_binding_store is None:
            return
        await self._runtime_binding_store.mark_cleanup_complete(
            state.binding.runtimeBindingRef,
            expected_revision=state.revision,
            expected_fencing_generation=state.fencing_generation,
        )

    async def _claim_cleanup(self, lease: Any) -> Any | None:
        """Claim the observed lease generation before cleanup side effects."""

        if lease.status not in CLEANUP_CLAIMABLE_HOST_STATES:
            return lease
        return await self._repository.claim_host_lease_cleanup(
            lease.lease_id,
            expected_status=lease.status,
            expected_last_heartbeat_at=lease.last_heartbeat_at,
            ttl_seconds=int(self._heartbeat_timeout.total_seconds()),
        )

    async def _release_provider_lease(
        self,
        *,
        binding: Any,
        lease: Any,
        fencing_generation: int | None = None,
        run_id: str | None = None,
        evidence_identity: str | None = None,
    ) -> bool:
        """Release capacity only after the credential-bearing host is stopped.

        MoonLadderStudios/MoonMind#1089: the janitor is the designated
        executing owner for the hosts it stops. It completes the manager's
        cleanup obligation through ``report_cleanup_verified`` with positive
        teardown evidence (the stop above already succeeded), quoting the
        acquired fence. A terminal workflow, a missing Temporal record, or a
        database tombstone is never teardown evidence and never reaches the
        ledger through this path. When no fenced cleanup obligation exists
        for this lease yet, this returns False without signaling: the host
        stays stopped but the slot stays spent until the manager requests
        cleanup and the drain path completes it with the acquired fence.
        Omnigent host leases are created exclusively
        for ``execution_omnigent`` capacity. The ProviderProfileManager
        deliberately uses the deterministic owner token as its lease ID, so the
        durable ``provider_lease_id`` is also the release authority needed
        after an activity process disappears.
        """

        if self._lease_client is None:
            return False
        provider_lease_id = str(lease.provider_lease_id or "").strip()
        runtime_id = str(
            binding.credential_mount_ref.auth_volume_ref.runtime_id or ""
        ).strip()
        if not provider_lease_id or not runtime_id:
            raise ValueError(
                "host lease is missing Provider Profile release authority"
            )
        resolved_fence = fencing_generation
        resolved_run = run_id
        resolved_identity = evidence_identity
        if (
            resolved_fence is None
            or resolved_run is None
            or resolved_identity is None
        ) and hasattr(self._lease_client, "get_cleanup_obligations"):
            try:
                claims = await self._lease_client.get_cleanup_obligations(
                    runtime_id=runtime_id
                )
            except Exception:
                claims = []
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                if str(claim.get("lease_id") or "") != provider_lease_id:
                    continue
                if resolved_fence is None:
                    try:
                        resolved_fence = int(claim.get("fencing_generation") or 0) or None
                    except (TypeError, ValueError):
                        resolved_fence = None
                if resolved_run is None:
                    resolved_run = str(claim.get("runId") or "") or None
                if resolved_identity is None:
                    resolved_identity = str(claim.get("evidenceIdentity") or "") or None
                break
        report = getattr(self._lease_client, "report_cleanup_verified", None)
        if callable(report):
            if not resolved_fence:
                # No fenced cleanup obligation exists yet for this lease.
                # Reporting verified teardown without the held fence would be
                # ignored by the manager while this helper returned True,
                # falsely recording the provider slot as released. Leave the
                # slot spent: the manager's verification loop will request
                # cleanup, then the drain path completes it with the fence.
                return False
            await report(
                CredentialLease(
                    profile_id=lease.provider_profile_id,
                    runtime_id=runtime_id,
                    lease_id=provider_lease_id,
                    owner_id=provider_lease_id,
                    purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                    fencing_generation=resolved_fence,
                    evidence_identity=resolved_identity,
                ),
                run_id=resolved_run,
                verified_by="omnigent-oauth-host-janitor",
            )
            return True
        await self._lease_client.release_lease(
            CredentialLease(
                profile_id=lease.provider_profile_id,
                runtime_id=runtime_id,
                lease_id=provider_lease_id,
                owner_id=provider_lease_id,
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            )
        )
        return True

    async def drain_manager_cleanup_claims(
        self,
        runtime_id: str,
        claims: list[dict[str, Any]],
        *,
        max_claims: int = 10,
    ) -> dict[str, Any]:
        """Complete manager cleanup claims whose host this janitor can stop.

        MoonLadderStudios/MoonMind#1089: consumes the manager's published
        ``cleanup_obligations`` (stable ``claim_id`` = lease ID + acquired
        fence) using only the existing host-stop machinery. Each bounded claim
        whose ``provider_lease_id`` maps to a host lease owned here is stopped
        through the existing authority-checked path and completed through
        ``report_cleanup_verified`` with the claim's fence and admitted
        identity. Claims without a host lease here are left to their owning
        AgentRun, realizer, or operator — a missing row is never proof that a
        consumer stopped. Late or replacement claims never touch a new owner's
        resources because the fence and identity are quoted back.
        """

        from moonmind.omnigent.oauth_hosts import deterministic_host_lease_id

        actions: list[dict[str, Any]] = []
        for claim in list(claims or [])[: max(1, int(max_claims))]:
            if not isinstance(claim, dict):
                continue
            provider_lease_id = str(claim.get("lease_id") or "").strip()
            claim_id = str(claim.get("claim_id") or "").strip() or (
                f"{provider_lease_id}:{claim.get('fencing_generation') or 0}"
            )
            if not provider_lease_id:
                continue
            try:
                fence_raw = claim.get("fencing_generation")
                fence = int(fence_raw) if fence_raw is not None else None
            except (TypeError, ValueError):
                fence = None
            host_lease_id = deterministic_host_lease_id(provider_lease_id)
            try:
                host_lease = await self._repository.get_host_lease(host_lease_id)
            except Exception as exc:
                actions.append(
                    {
                        "claimId": claim_id,
                        "providerLeaseId": provider_lease_id,
                        "action": "cleanup_claim_lookup_failed",
                        "errorCode": type(exc).__name__,
                    }
                )
                continue
            if host_lease is None:
                continue
            if getattr(host_lease, "provider_profile_id", "") != claim.get("profile_id"):
                # The claim moved to a replacement profile owner; never touch
                # a new owner's resources from a stale claim.
                actions.append(
                    {
                        "claimId": claim_id,
                        "providerLeaseId": provider_lease_id,
                        "action": "cleanup_claim_skipped_replacement_owner",
                    }
                )
                continue
            try:
                binding = await self._repository.validate_binding(
                    host_lease.binding_ref
                )
                runtime_binding_state = (
                    await self._runtime_binding_cleanup_authority(
                        binding=binding, lease=host_lease
                    )
                )
                claimed = await self._claim_cleanup(host_lease)
                if claimed is None:
                    actions.append(
                        {
                            "claimId": claim_id,
                            "providerLeaseId": provider_lease_id,
                            "action": "cleanup_claim_deferred_contended",
                        }
                    )
                    continue
                host_lease = claimed
                cleanup_evidence = await self._stop_host_with_authority(
                    binding=binding, lease=host_lease
                )
                stopped_lease = await self._repository.mark_host_lease_stopped(
                    host_lease.lease_id
                )
                if stopped_lease is not None:
                    host_lease = stopped_lease
                provider_released = await self._release_provider_lease(
                    binding=binding,
                    lease=host_lease,
                    fencing_generation=fence,
                    run_id=str(claim.get("runId") or "") or None,
                    evidence_identity=str(claim.get("evidenceIdentity") or "") or None,
                )
                await self._record_terminal_cleanup(
                    lease=host_lease,
                    completed=True,
                    cleanup_evidence=cleanup_evidence,
                    lease_released=provider_released,
                )
                await self._complete_runtime_binding_cleanup(runtime_binding_state)
                if provider_released:
                    actions.append(
                        {
                            "claimId": claim_id,
                            "providerLeaseId": provider_lease_id,
                            "action": "cleanup_claim_completed_verified",
                            "providerLeaseReleased": provider_released,
                        }
                    )
                else:
                    # Host is stopped but the slot stays spent: no fenced
                    # verified-teardown outcome was confirmed (e.g. the claim
                    # carried no usable fence). A later drain with the fenced
                    # claim completes it; never record an unconfirmed release.
                    actions.append(
                        {
                            "claimId": claim_id,
                            "providerLeaseId": provider_lease_id,
                            "action": "cleanup_claim_failed",
                            "errorCode": "cleanup_not_verified",
                        }
                    )
            except Exception as exc:
                try:
                    await self._record_terminal_cleanup(
                        lease=host_lease,
                        completed=False,
                        error=exc,
                        lease_released=False,
                    )
                except Exception:
                    # Best-effort terminal audit record; the original cleanup
                    # failure is already reported via cleanup_claim_failed below,
                    # so a secondary recording failure must not mask it.
                    pass
                actions.append(
                    {
                        "claimId": claim_id,
                        "providerLeaseId": provider_lease_id,
                        "action": "cleanup_claim_failed",
                        "errorCode": str(
                            getattr(exc, "code", "") or type(exc).__name__
                        ),
                    }
                )
        return {
            "status": (
                "degraded"
                if any(item["action"].endswith("_failed") for item in actions)
                else "completed"
            ),
            "actions": actions,
            "count": len(actions),
        }

    async def _cleanup_authority(self, lease: Any) -> dict[str, Any] | None:
        authority = None
        if self._run_store is not None and hasattr(
            self._run_store, "get_egress_cleanup_authority"
        ):
            authority = await self._run_store.get_egress_cleanup_authority(
                host_lease_ref=lease.lease_id
            )
        launch = getattr(lease, "effective_launch_snapshot", None)
        requires_authority = bool(
            isinstance(launch, dict) and launch.get("enforcedEgress") is True
        )
        authority_required = bool(
            isinstance(launch, dict)
            and launch.get("egressCleanupAuthorityRequired") is True
        )
        credential_only_preflight = bool(
            getattr(lease, "lease_purpose", None) == "credential_validation"
            and not getattr(lease, "omnigent_host_id", None)
            and not getattr(lease, "omnigent_session_id", None)
            and not getattr(lease, "bridge_session_id", None)
        )
        if authority is None and credential_only_preflight:
            # Credential validators are launched with ``--network none`` and
            # never cross the restricted-egress attachment boundary.  Their
            # lease still carries the selected launch snapshot for image and
            # credential-generation authority, so inspecting only
            # ``enforcedEgress`` would incorrectly demand evidence that cannot
            # exist and strand Provider Profile capacity forever after a crash.
            return None
        if authority is None and requires_authority and authority_required:
            raise ValueError(
                "restricted-egress host cleanup authority is unavailable"
            )
        if (
            authority is None
            and requires_authority
            and "egressCleanupAuthorityRequired" in launch
            and not authority_required
        ):
            raise ValueError(
                "restricted-egress host cleanup authority requirement is invalid"
            )
        return authority

    async def _stop_host_with_authority(
        self, *, binding: Any, lease: Any
    ) -> dict[str, Any]:
        authority = await self._cleanup_authority(lease)
        if authority is None:
            result = await self._runtime.stop_host(
                binding=binding, host_lease=lease
            )
            cleanup = dict(result or {})
            launch = getattr(lease, "effective_launch_snapshot", None)
            if isinstance(launch, dict) and launch.get("enforcedEgress") is True:
                # Leases written before the cleanup-authority marker cannot have
                # the bridge metadata introduced with it. Stop and objectively
                # reconcile the credential-bearing host without claiming egress
                # conformance evidence, then let release-last ordering proceed.
                cleanup["cleanupAuthorityDisposition"] = "pre_upgrade_cutover"
            return cleanup
        if self._artifact_gateway is None:
            raise ValueError(
                "restricted-egress host cleanup evidence publisher is unavailable"
            )
        request_identity = authority.get("evidenceRequest")
        effective_launch = authority.get("effectiveLaunch")
        egress_evidence = authority.get("egressEvidence")
        if not isinstance(request_identity, dict) or not isinstance(
            effective_launch, dict
        ) or not isinstance(egress_evidence, dict):
            raise ValueError("restricted-egress host cleanup authority is incomplete")
        return await self._runtime.stop_host(
            binding=binding,
            host_lease=lease,
            effective_launch=effective_launch,
            egress_evidence=egress_evidence,
            launch_evidence_ref=str(authority.get("launchEvidenceRef") or "") or None,
            evidence_request=OmnigentEgressEvidenceRequestIdentity.from_mapping(
                request_identity
            ),
            artifact_gateway=self._artifact_gateway,
        )

    async def _record_terminal_cleanup(
        self,
        *,
        lease: Any,
        completed: bool,
        cleanup_evidence: dict[str, Any] | None = None,
        error: Exception | None = None,
        lease_released: bool | None = None,
    ) -> None:
        if self._run_store is None or not hasattr(
            self._run_store, "record_terminal_cleanup"
        ):
            return
        evidence = dict(cleanup_evidence or {})
        error_evidence = getattr(error, "cleanup_evidence", None)
        if isinstance(error_evidence, dict):
            evidence.update(error_evidence)
        evidence_ref = str(
            evidence.get("evidenceRef")
            or getattr(error, "egress_evidence_ref", None)
            or ""
        ).strip() or None
        await self._run_store.record_terminal_cleanup(
            host_lease_ref=lease.lease_id,
            completed=completed,
            code=type(error).__name__ if error is not None else None,
            summary=str(error or ""),
            egress_evidence_ref=evidence_ref,
            launch_evidence_ref=str(evidence.get("launchEvidenceRef") or "") or None,
            lease_released=lease_released,
        )

    async def run_action(
        self,
        *,
        action_kind: str,
        profile_id: str,
        host_lease_ref: str,
        expected_host_state: str | None,
        request_id: str,
    ) -> dict[str, Any]:
        """Apply one lease-scoped remediation operation with before/after evidence."""

        supported = {
            "provider_profile.evict_stale_lease",
            "host.drain",
            "host.stop",
            "host.restart",
            "host.remove",
            "host_lease.reconcile_stale",
        }
        if action_kind not in supported:
            raise ValueError(f"unsupported Omnigent remediation action: {action_kind}")
        lease = await self._repository.get_host_lease(host_lease_ref)
        if lease is None:
            raise ValueError("host lease does not exist")
        if lease.provider_profile_id != profile_id:
            raise ValueError("host lease is not owned by the Provider Profile")
        before_state = lease.status
        if expected_host_state and expected_host_state != before_state:
            raise ValueError("expectedHostState does not match the current host lease")
        binding = await self._repository.validate_binding(lease.binding_ref)
        now = datetime.now(UTC)
        stale = (
            lease.expires_at <= now
            or lease.last_heartbeat_at <= now - self._heartbeat_timeout
        )
        cleanup_evidence: dict[str, Any] = {}

        if action_kind == "host.drain":
            if before_state in {"draining", "stopped", "failed"}:
                return self._action_result(
                    action_kind, request_id, profile_id, lease, before_state
                )
            claimed = await self._claim_cleanup(lease)
            if claimed is None:
                raise OmnigentOAuthHostError(
                    "host lease changed concurrently before cleanup claim"
                )
            lease = claimed
        elif action_kind == "host.restart":
            raise ValueError(
                "host.restart is unsupported until the owning launch path can "
                "return terminal generation evidence"
            )
        elif action_kind == "provider_profile.evict_stale_lease":
            if not stale:
                raise ValueError("Provider Profile host lease is not stale")
        elif action_kind == "host_lease.reconcile_stale":
            if not stale:
                return self._action_result(
                    action_kind, request_id, profile_id, lease, before_state
                )

        if action_kind in {
            "host.stop",
            "host.remove",
            "provider_profile.evict_stale_lease",
            "host_lease.reconcile_stale",
        }:
            if lease.status == "draining" and not stale:
                raise OmnigentOAuthHostError(
                    "host lease cleanup is already owned by another worker"
                )
            runtime_binding_state = (
                await self._runtime_binding_cleanup_authority(
                    binding=binding, lease=lease
                )
            )
            claimed = await self._claim_cleanup(lease)
            if claimed is None:
                raise OmnigentOAuthHostError(
                    "host lease changed concurrently before cleanup claim"
                )
            lease = claimed
            try:
                # Even an already-absent attachment crosses ``stop_host``: that
                # owner publishes independently resolvable terminal evidence
                # before Provider Profile capacity can be released.
                cleanup_evidence = await self._stop_host_with_authority(
                    binding=binding, lease=lease
                )
                stopped_lease = await self._repository.mark_host_lease_stopped(
                    lease.lease_id
                )
                if stopped_lease is not None:
                    lease = stopped_lease
                provider_released = await self._release_provider_lease(
                    binding=binding, lease=lease
                )
                await self._record_terminal_cleanup(
                    lease=lease,
                    completed=True,
                    cleanup_evidence=cleanup_evidence,
                    lease_released=provider_released,
                )
                await self._complete_runtime_binding_cleanup(
                    runtime_binding_state
                )
            except Exception as exc:
                await self._record_terminal_cleanup(
                    lease=lease,
                    completed=False,
                    cleanup_evidence=cleanup_evidence,
                    error=exc,
                    lease_released=False,
                )
                raise

        result = self._action_result(
            action_kind, request_id, profile_id, lease, before_state
        )
        evidence_ref = str(cleanup_evidence.get("evidenceRef") or "").strip()
        launch_evidence_ref = str(
            cleanup_evidence.get("launchEvidenceRef") or ""
        ).strip()
        if launch_evidence_ref:
            result["beforeEvidenceRefs"].append(launch_evidence_ref)
        if evidence_ref:
            result["afterEvidenceRefs"].append(evidence_ref)
        if action_kind == "host.remove" and cleanup_evidence:
            result["status"] = "applied"
        return result

    @staticmethod
    def _action_result(
        action_kind: str,
        request_id: str,
        profile_id: str,
        lease: Any,
        before_state: str,
    ) -> dict[str, Any]:
        return {
            "status": "applied" if lease.status != before_state else "no_op",
            "actionKind": action_kind,
            "requestId": request_id,
            "hostLeaseRef": lease.lease_id,
            "providerProfileId": profile_id,
            "before": {"status": before_state, "bindingRef": lease.binding_ref},
            "after": {"status": lease.status, "bindingRef": lease.binding_ref},
            "beforeEvidenceRefs": [
                f"omnigent-host-lease:{lease.lease_id}:state:{before_state}"
            ],
            "afterEvidenceRefs": [
                f"omnigent-host-lease:{lease.lease_id}:state:{lease.status}"
            ],
        }

    async def run(
        self, *, profile_id: str | None = None, force: bool = False,
        runtime_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        actions: list[dict[str, Any]] = []
        observed_runtime_ids: set[str] = {
            str(runtime_id).strip() for runtime_id in (runtime_ids or []) if str(runtime_id).strip()
        }
        if force and profile_id:
            binding = await self._repository.get_binding_for_profile(profile_id)
            if binding is not None and not binding.host_launch_profile_ref:
                await self._runtime.stop_static_host(binding=binding)
                actions.append(
                    {
                        "hostBindingRef": binding.binding_ref,
                        "action": "static_host_stopped",
                    }
                )
        leases = await self._repository.list_active_host_leases(failures=actions)
        terminal_provider_leases = (
            await self._repository.list_terminal_host_leases_with_active_provider_capacity(
                failures=actions
            )
            if hasattr(
                self._repository,
                "list_terminal_host_leases_with_active_provider_capacity",
            )
            else []
        )
        terminal_provider_lease_refs = {
            lease.lease_id for lease in terminal_provider_leases
        }
        leases = list(
            {lease.lease_id: lease for lease in [*leases, *terminal_provider_leases]}.values()
        )
        cleanup_required = (
            await self._run_store.cleanup_required_host_lease_refs()
            if self._run_store is not None
            else set()
        )
        now = datetime.now(UTC)
        reconciliation_required = (
            await self._run_store.embedded_reconciliation_host_lease_refs(
                abandoned_before=now - self._heartbeat_timeout
            )
            if self._run_store is not None
            and hasattr(self._run_store, "embedded_reconciliation_host_lease_refs")
            else {}
        )
        known_containers = {
            lease.container_name: lease for lease in leases if lease.container_name
        }
        for lease in leases:
            try:
                if profile_id and lease.provider_profile_id != profile_id:
                    continue
                expired = lease.expires_at <= now
                stale = lease.last_heartbeat_at <= now - self._heartbeat_timeout
                terminal_cleanup = lease.lease_id in cleanup_required
                terminal_provider_cleanup = (
                    lease.lease_id in terminal_provider_lease_refs
                )
                reconciliation_action = reconciliation_required.get(lease.lease_id)
                missing = bool(
                    lease.container_name
                    and not await self._runtime.container_exists(lease.container_name)
                )
                # ``allocating`` and ``starting`` precede the container
                # materialization authority handoff. A deterministic container name
                # exists on the lease during that window, but the container is not
                # required to exist yet. Absence alone is therefore not cleanup
                # evidence until the coordinator crosses into ready/assigned. A
                # stale/expired lease or an explicit durable reconciliation signal
                # still permits cleanup of an abandoned launch.
                missing_requires_cleanup = bool(
                    missing and lease.status not in {"allocating", "starting"}
                )
                if (
                    not force
                    and not expired
                    and not missing_requires_cleanup
                    and not stale
                    and not terminal_cleanup
                    and not terminal_provider_cleanup
                    and not reconciliation_action
                ):
                    continue
                # A fresh draining lease already has a cleanup owner. Let that owner
                # finish; only a stale/expired pass may recover an abandoned drain.
                if (
                    lease.status == "draining"
                    and not force
                    and not expired
                    and not stale
                ):
                    continue
                binding = await self._repository.validate_binding(lease.binding_ref)
                try:
                    observed_runtime_id = str(
                        binding.credential_mount_ref.auth_volume_ref.runtime_id or ""
                    ).strip()
                except Exception:
                    observed_runtime_id = ""
                if observed_runtime_id:
                    observed_runtime_ids.add(observed_runtime_id)
                runtime_binding_state = await self._runtime_binding_cleanup_authority(
                    binding=binding, lease=lease
                )
                if lease.status in CLEANUP_CLAIMABLE_HOST_STATES:
                    claimed = await self._claim_cleanup(lease)
                    if claimed is None:
                        # The coordinator advanced state or heartbeat authority
                        # after this janitor pass observed it. Reconcile from the
                        # next durable scan; never clean from the stale snapshot.
                        continue
                    lease = claimed
                if lease.omnigent_session_id:
                    try:
                        await self._client.get_session(lease.omnigent_session_id)
                        await self._client.interrupt(lease.omnigent_session_id)
                        await self._client.stop_session(lease.omnigent_session_id)
                    except Exception as exc:
                        actions.append(
                            {
                                "hostLeaseRef": lease.lease_id,
                                "omnigentSessionRef": lease.omnigent_session_id,
                                "action": "session_cleanup_failed",
                                "errorCode": type(exc).__name__,
                            }
                        )
                cleanup_evidence: dict[str, Any] = {}
                try:
                    cleanup_evidence = await self._stop_host_with_authority(
                        binding=binding, lease=lease
                    )
                    stopped_lease = await self._repository.mark_host_lease_stopped(
                        lease.lease_id
                    )
                    if stopped_lease is not None:
                        lease = stopped_lease
                    provider_released = await self._release_provider_lease(
                        binding=binding, lease=lease
                    )
                except Exception as exc:
                    await self._record_terminal_cleanup(
                        lease=lease,
                        completed=False,
                        cleanup_evidence=cleanup_evidence,
                        error=exc,
                        lease_released=False,
                    )
                    raise
                await self._record_terminal_cleanup(
                    lease=lease,
                    completed=True,
                    cleanup_evidence=cleanup_evidence,
                    lease_released=provider_released,
                )
                await self._complete_runtime_binding_cleanup(runtime_binding_state)
                actions.append(
                    {
                        "hostLeaseRef": lease.lease_id,
                        "action": (
                            "expired_cleanup"
                            if expired
                            else (
                                "stale_heartbeat_cleanup"
                                if stale
                                else (
                                    "runner_exit_cleanup"
                                    if terminal_cleanup
                                    else (
                                        "provider_lease_reconciliation"
                                        if terminal_provider_cleanup
                                        else (
                                            reconciliation_action
                                            or "missing_container_repair"
                                        )
                                    )
                                )
                            )
                        ),
                        "providerLeaseReleased": provider_released,
                        "egressEvidenceRef": cleanup_evidence.get("evidenceRef"),
                        "egressLaunchEvidenceRef": cleanup_evidence.get(
                            "launchEvidenceRef"
                        ),
                    }
                )
            except Exception as exc:
                # A historical authority mismatch remains fenced, but cannot
                # prevent cleanup of leases with independent valid authority.
                actions.append(
                    {
                        "hostLeaseRef": lease.lease_id,
                        "action": "cleanup_failed",
                        "errorCode": str(
                            getattr(exc, "code", "") or type(exc).__name__
                        ),
                    }
                )
        # MoonLadderStudios/MoonMind#1089: drain manager cleanup obligations
        # whose host this janitor can stop. Bounded per runtime; a lookup
        # failure never fails the host cleanup above, and claims without a
        # host lease here are left to their owning AgentRun, realizer, or
        # operator.
        if self._lease_client is not None and hasattr(
            self._lease_client, "get_cleanup_obligations"
        ):
            for runtime_id in sorted(observed_runtime_ids):
                try:
                    claims = await self._lease_client.get_cleanup_obligations(
                        runtime_id=runtime_id
                    )
                except Exception as exc:
                    actions.append(
                        {
                            "runtimeId": runtime_id,
                            "action": "cleanup_claim_drain_failed",
                            "errorCode": type(exc).__name__,
                        }
                    )
                    continue
                if not claims:
                    continue
                try:
                    drained = await self.drain_manager_cleanup_claims(
                        runtime_id, claims, max_claims=10
                    )
                except Exception as exc:
                    actions.append(
                        {
                            "runtimeId": runtime_id,
                            "action": "cleanup_claim_drain_failed",
                            "errorCode": type(exc).__name__,
                        }
                    )
                    continue
                actions.extend(drained.get("actions") or [])
        for container_name in await self._runtime.list_managed_containers():
            try:
                if container_name in known_containers:
                    continue
                host_lease_ref = await self._runtime.managed_container_host_lease_ref(
                    container_name
                )
                if host_lease_ref:
                    live_lease = await self._repository.get_host_lease(host_lease_ref)
                    if live_lease is not None:
                        # The lease may have been created after the initial scan.
                        # Leave every lease-owned container to the claimed cleanup
                        # path on the next pass; raw orphan removal has no authority
                        # to race a coordinator or cleanup owner.
                        continue
                # No durable lease can resume or publish authority for a true orphan.
                # The runtime revalidates the deployment ownership label before
                # removing the credential-bearing resource.
                await self._runtime.remove_container(container_name)
                actions.append(
                    {
                        "containerName": container_name,
                        "action": "orphan_container_removed",
                        "providerLeaseReleased": False,
                    }
                )
            except Exception as exc:
                actions.append(
                    {
                        "containerName": container_name,
                        "action": "cleanup_failed",
                        "errorCode": type(exc).__name__,
                    }
                )
        return {
            "status": (
                "degraded"
                if any(item["action"].endswith("_failed") for item in actions)
                else "completed"
            ),
            "actions": actions,
            "count": len(actions),
        }


__all__ = ["OmnigentOAuthHostJanitor"]
