from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostError


class _Repository:
    def __init__(self, lease):
        self.lease = lease
        self.cleanup_claims: list[str] = []
        self.stopped: list[str] = []
        self.order: list[str] = []

    async def list_active_host_leases(self):
        return [self.lease]

    async def list_terminal_host_leases_with_active_provider_capacity(self):
        return []

    async def validate_binding(self, _binding_ref):
        return SimpleNamespace(
            credential_mount_ref=SimpleNamespace(
                auth_volume_ref=SimpleNamespace(runtime_id="codex_cli")
            )
        )

    async def get_binding_for_profile(self, _profile_id):
        return None

    async def get_host_lease(self, lease_id):
        return self.lease if lease_id == self.lease.lease_id else None

    async def mark_host_lease_stopped(self, lease_id):
        self.order.append("lease_released")
        self.stopped.append(lease_id)
        self.lease.status = "stopped"
        return self.lease

    async def claim_host_lease_cleanup(
        self, lease_id, *, expected_status, expected_last_heartbeat_at,
        ttl_seconds=90,
    ):
        assert lease_id == self.lease.lease_id
        if (
            self.lease.status != expected_status
            or self.lease.last_heartbeat_at != expected_last_heartbeat_at
        ):
            return None
        self.order.append("cleanup_claimed")
        self.cleanup_claims.append(lease_id)
        self.lease.status = "draining"
        self.lease.last_heartbeat_at = datetime.now(UTC)
        self.lease.expires_at = self.lease.last_heartbeat_at + timedelta(
            seconds=ttl_seconds
        )
        return self.lease

    async def transition_host_lease(
        self, lease_id, *, expected_status, new_status
    ):
        assert lease_id == self.lease.lease_id
        assert self.lease.status == expected_status
        self.lease.status = new_status
        return self.lease

    async def restart_host_lease(self, lease_id):
        assert lease_id == self.lease.lease_id
        self.lease.status = "starting"
        return self.lease


class _Runtime:
    def __init__(self, order=None):
        self.stopped = 0
        self.removed: list[str] = []
        self.order = order if order is not None else []
        self.stop_kwargs: list[dict] = []
        self.static_stops = 0
        self.managed_containers: list[str] = []
        self.container_lease_refs: dict[str, str | None] = {}

    async def container_exists(self, _name):
        return True

    async def stop_host(self, **kwargs):
        self.order.append("host_stopped")
        self.stopped += 1
        self.stop_kwargs.append(kwargs)
        if kwargs.get("effective_launch") is not None:
            return {
                "evidenceRef": "artifact://terminal-egress",
                "launchEvidenceRef": kwargs.get("launch_evidence_ref"),
            }
        return {}

    async def list_managed_containers(self):
        return list(self.managed_containers)

    async def managed_container_host_lease_ref(self, name):
        return self.container_lease_refs.get(name)

    async def stop_static_host(self, **_kwargs):
        self.static_stops += 1

    async def remove_container(self, name):
        self.removed.append(name)


class _Client:
    async def get_session(self, _session_id):
        return {}

    async def interrupt(self, _session_id):
        return {}

    async def stop_session(self, _session_id):
        return {}


class _LeaseClient:
    def __init__(self, order=None):
        self.released = []
        self.order = order if order is not None else []

    async def release_lease(self, lease):
        self.order.append("provider_released")
        self.released.append(lease)


def _lease(*, heartbeat_age: int = 0):
    now = datetime.now(UTC)
    return SimpleNamespace(
        lease_id="lease-1",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        binding_ref="binding-1",
        container_name="host-1",
        omnigent_session_id="session-1",
        last_heartbeat_at=now - timedelta(seconds=heartbeat_age),
        expires_at=now + timedelta(hours=1),
        status="ready",
    )


@pytest.mark.asyncio
async def test_action_applies_only_named_lease_with_expected_state_evidence() -> None:
    repository = _Repository(_lease())
    runtime = _Runtime()

    result = await OmnigentOAuthHostJanitor(
        repository=repository, runtime=runtime, client=_Client()
    ).run_action(
        action_kind="host.stop",
        profile_id="profile-1",
        host_lease_ref="lease-1",
        expected_host_state="ready",
        request_id="request-1",
    )

    assert result["status"] == "applied"
    assert result["before"]["status"] == "ready"
    assert result["after"]["status"] == "stopped"
    assert result["requestId"] == "request-1"
    assert runtime.stopped == 1


@pytest.mark.asyncio
async def test_action_rejects_stale_expected_state_before_mutation() -> None:
    repository = _Repository(_lease())
    runtime = _Runtime()

    with pytest.raises(ValueError, match="expectedHostState"):
        await OmnigentOAuthHostJanitor(
            repository=repository, runtime=runtime, client=_Client()
        ).run_action(
            action_kind="host.stop",
            profile_id="profile-1",
            host_lease_ref="lease-1",
            expected_host_state="draining",
            request_id="request-1",
        )

    assert runtime.stopped == 0


@pytest.mark.asyncio
async def test_action_reuses_durable_launch_authority_and_binds_terminal_evidence() -> None:
    lease = _lease()
    lease.effective_launch_snapshot = {"enforcedEgress": True}
    repository = _Repository(lease)
    runtime = _Runtime(repository.order)
    lease_client = _LeaseClient(repository.order)
    terminal_records: list[dict] = []

    class RunStore:
        async def get_egress_cleanup_authority(self, *, host_lease_ref):
            assert host_lease_ref == "lease-1"
            return {
                "effectiveLaunch": {
                    "snapshotRef": "omnigent-launch:sha256:" + "a" * 64,
                    "enforcedEgress": True,
                },
                "egressEvidence": {
                    "attachmentIdentity": "host-1",
                    "validationResult": "passed",
                },
                "launchEvidenceRef": "artifact://launch-egress",
                "evidenceRequest": {
                    "correlationId": "workflow-1",
                    "idempotencyKey": "idem-1",
                    "remediation": True,
                },
            }

        async def record_terminal_cleanup(self, **kwargs):
            terminal_records.append(kwargs)

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
        run_store=RunStore(),
        lease_client=lease_client,
        artifact_gateway=object(),
    ).run_action(
        action_kind="host.stop",
        profile_id="profile-1",
        host_lease_ref="lease-1",
        expected_host_state="ready",
        request_id="request-egress",
    )

    cleanup_call = runtime.stop_kwargs[0]
    assert cleanup_call["effective_launch"]["enforcedEgress"] is True
    assert cleanup_call["egress_evidence"]["attachmentIdentity"] == "host-1"
    assert cleanup_call["launch_evidence_ref"] == "artifact://launch-egress"
    assert cleanup_call["evidence_request"].remediation_workspace is not None
    assert cleanup_call["artifact_gateway"] is not None
    assert result["beforeEvidenceRefs"][-1] == "artifact://launch-egress"
    assert result["afterEvidenceRefs"][-1] == "artifact://terminal-egress"
    assert terminal_records[-1]["completed"] is True
    assert terminal_records[-1]["egress_evidence_ref"] == (
        "artifact://terminal-egress"
    )
    assert repository.order == [
        "cleanup_claimed",
        "host_stopped",
        "lease_released",
        "provider_released",
    ]


@pytest.mark.asyncio
async def test_action_cleanup_failure_records_published_evidence_before_raise() -> None:
    lease = _lease()
    lease.effective_launch_snapshot = {"enforcedEgress": True}
    repository = _Repository(lease)
    terminal_records: list[dict] = []

    class Runtime(_Runtime):
        async def stop_host(self, **kwargs):
            self.stop_kwargs.append(kwargs)
            raise OmnigentOAuthHostError(
                "cleanup incomplete",
                egress_evidence_ref="artifact://terminal-failure",
                cleanup_evidence={
                    "evidenceRef": "artifact://terminal-failure",
                    "launchEvidenceRef": "artifact://launch-egress",
                },
            )

    class RunStore:
        async def get_egress_cleanup_authority(self, **_kwargs):
            return {
                "effectiveLaunch": {"enforcedEgress": True},
                "egressEvidence": {"attachmentIdentity": "host-1"},
                "launchEvidenceRef": "artifact://launch-egress",
                "evidenceRequest": {
                    "correlationId": "workflow-1",
                    "idempotencyKey": "idem-1",
                    "remediation": False,
                },
            }

        async def record_terminal_cleanup(self, **kwargs):
            terminal_records.append(kwargs)

    with pytest.raises(OmnigentOAuthHostError, match="cleanup incomplete"):
        await OmnigentOAuthHostJanitor(
            repository=repository,
            runtime=Runtime(),
            client=_Client(),
            run_store=RunStore(),
            lease_client=_LeaseClient(),
            artifact_gateway=object(),
        ).run_action(
            action_kind="host.remove",
            profile_id="profile-1",
            host_lease_ref="lease-1",
            expected_host_state="ready",
            request_id="request-failure",
        )

    assert terminal_records[-1]["completed"] is False
    assert terminal_records[-1]["egress_evidence_ref"] == (
        "artifact://terminal-failure"
    )
    assert repository.stopped == []


@pytest.mark.asyncio
async def test_host_remove_crosses_host_cleanup_owner() -> None:
    repository = _Repository(_lease())
    runtime = _Runtime()

    result = await OmnigentOAuthHostJanitor(
        repository=repository, runtime=runtime, client=_Client()
    ).run_action(
        action_kind="host.remove",
        profile_id="profile-1",
        host_lease_ref="lease-1",
        expected_host_state="ready",
        request_id="request-remove",
    )

    assert result["status"] == "applied"
    assert runtime.removed == []
    assert runtime.stopped == 1
    assert repository.stopped == ["lease-1"]


@pytest.mark.asyncio
async def test_stale_lease_eviction_rejects_fresh_lease() -> None:
    repository = _Repository(_lease())

    with pytest.raises(ValueError, match="not stale"):
        await OmnigentOAuthHostJanitor(
            repository=repository, runtime=_Runtime(), client=_Client()
        ).run_action(
            action_kind="provider_profile.evict_stale_lease",
            profile_id="profile-1",
            host_lease_ref="lease-1",
            expected_host_state="ready",
            request_id="request-evict",
        )


@pytest.mark.asyncio
async def test_janitor_reconciles_stale_heartbeat_after_restart() -> None:
    repository = _Repository(_lease(heartbeat_age=121))
    runtime = _Runtime(repository.order)

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
        heartbeat_timeout_seconds=90,
    ).run()

    assert result["actions"][-1]["action"] == "stale_heartbeat_cleanup"
    assert repository.stopped == ["lease-1"]
    assert runtime.stopped == 1
    assert repository.order[:2] == ["cleanup_claimed", "host_stopped"]


@pytest.mark.asyncio
async def test_janitor_ignores_missing_container_during_fresh_launch() -> None:
    lease = _lease()
    lease.status = "starting"
    lease.omnigent_session_id = None
    repository = _Repository(lease)
    runtime = _Runtime(repository.order)

    async def missing_container(_name):
        return False

    runtime.container_exists = missing_container

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
        heartbeat_timeout_seconds=90,
    ).run()

    assert result == {"status": "completed", "actions": [], "count": 0}
    assert repository.cleanup_claims == []
    assert repository.stopped == []
    assert runtime.stopped == 0


@pytest.mark.asyncio
async def test_janitor_claims_missing_container_after_launch_boundary() -> None:
    repository = _Repository(_lease())
    runtime = _Runtime(repository.order)

    async def missing_container(_name):
        return False

    runtime.container_exists = missing_container

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
    ).run()

    assert result["actions"][-1]["action"] == "missing_container_repair"
    assert repository.cleanup_claims == ["lease-1"]
    assert repository.order[:2] == ["cleanup_claimed", "host_stopped"]


@pytest.mark.asyncio
async def test_janitor_reloads_after_cleanup_claim_conflict() -> None:
    repository = _Repository(_lease(heartbeat_age=121))
    runtime = _Runtime(repository.order)

    async def lost_claim(*_args, **_kwargs):
        repository.lease.last_heartbeat_at = datetime.now(UTC)
        return None

    repository.claim_host_lease_cleanup = lost_claim

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
        heartbeat_timeout_seconds=90,
    ).run()

    assert result == {"status": "completed", "actions": [], "count": 0}
    assert repository.stopped == []
    assert runtime.stopped == 0


@pytest.mark.asyncio
async def test_janitor_consumes_durable_runner_exit_cleanup_handoff() -> None:
    repository = _Repository(_lease())
    runtime = _Runtime(repository.order)
    lease_client = _LeaseClient(repository.order)
    run_store = SimpleNamespace(
        cleanup_required_host_lease_refs=lambda: None,
    )

    async def cleanup_refs():
        return {"lease-1"}

    run_store.cleanup_required_host_lease_refs = cleanup_refs
    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
        run_store=run_store,
        lease_client=lease_client,
    ).run()

    assert result["actions"][-1]["action"] == "runner_exit_cleanup"
    assert repository.stopped == ["lease-1"]
    assert lease_client.released[0].lease_id == "provider-lease-1"
    assert lease_client.released[0].owner_id == "provider-lease-1"
    assert lease_client.released[0].runtime_id == "codex_cli"
    assert repository.order == [
        "cleanup_claimed",
        "host_stopped",
        "lease_released",
        "provider_released",
    ]


@pytest.mark.asyncio
async def test_janitor_releases_capacity_left_on_already_stopped_host() -> None:
    lease = _lease()
    lease.status = "stopped"
    repository = _Repository(lease)
    repository.list_active_host_leases = lambda: None

    async def no_active_leases():
        return []

    async def terminal_provider_leases():
        return [lease]

    repository.list_active_host_leases = no_active_leases
    repository.list_terminal_host_leases_with_active_provider_capacity = (
        terminal_provider_leases
    )
    runtime = _Runtime(repository.order)
    lease_client = _LeaseClient(repository.order)

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
        lease_client=lease_client,
    ).run()

    assert result["actions"][-1]["action"] == "provider_lease_reconciliation"
    assert repository.order == [
        "host_stopped",
        "lease_released",
        "provider_released",
    ]
    assert runtime.stopped == 1


@pytest.mark.asyncio
async def test_janitor_leaves_fresh_host_owned_by_active_session() -> None:
    repository = _Repository(_lease())
    runtime = _Runtime()

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
    ).run()

    assert result["count"] == 0
    assert repository.stopped == []
    assert runtime.stopped == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [
    "abandoned_launch_cleanup",
    "acknowledgement_without_binding_cleanup",
    "binding_without_tunnel_cleanup",
    "stale_binding_cleanup",
    "credential_generation_cleanup",
])
async def test_janitor_reconciles_each_durable_embedded_abandonment_class(
    action: str,
) -> None:
    repository = _Repository(_lease())
    runtime = _Runtime(repository.order)

    async def cleanup_refs():
        return set()

    async def reconciliation_refs(*, abandoned_before):
        assert abandoned_before < datetime.now(UTC)
        return {"lease-1": action}

    run_store = SimpleNamespace(
        cleanup_required_host_lease_refs=cleanup_refs,
        embedded_reconciliation_host_lease_refs=reconciliation_refs,
    )
    result = await OmnigentOAuthHostJanitor(
        repository=repository, runtime=runtime, client=_Client(), run_store=run_store,
    ).run()

    assert result["actions"][-1]["action"] == action
    assert repository.order == [
        "cleanup_claimed",
        "host_stopped",
        "lease_released",
    ]


@pytest.mark.asyncio
async def test_pre_upgrade_restricted_lease_uses_bounded_cleanup_cutover() -> None:
    lease = _lease()
    lease.effective_launch_snapshot = {"enforcedEgress": True}
    repository = _Repository(lease)
    runtime = _Runtime(repository.order)
    lease_client = _LeaseClient(repository.order)

    class RunStore:
        async def get_egress_cleanup_authority(self, **_kwargs):
            return None

        async def record_terminal_cleanup(self, **_kwargs):
            return None

    await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
        run_store=RunStore(),
        lease_client=lease_client,
    ).run_action(
        action_kind="host.stop",
        profile_id="profile-1",
        host_lease_ref="lease-1",
        expected_host_state="ready",
        request_id="legacy-cutover",
    )

    assert runtime.stop_kwargs[0].get("effective_launch") is None
    assert repository.order == [
        "cleanup_claimed",
        "host_stopped",
        "lease_released",
        "provider_released",
    ]


@pytest.mark.asyncio
async def test_current_restricted_lease_without_cleanup_authority_fails_closed() -> None:
    lease = _lease()
    lease.effective_launch_snapshot = {
        "enforcedEgress": True,
        "egressCleanupAuthorityRequired": True,
    }
    repository = _Repository(lease)
    runtime = _Runtime()

    class RunStore:
        async def get_egress_cleanup_authority(self, **_kwargs):
            return None

        async def record_terminal_cleanup(self, **_kwargs):
            return None

    with pytest.raises(ValueError, match="cleanup authority is unavailable"):
        await OmnigentOAuthHostJanitor(
            repository=repository,
            runtime=runtime,
            client=_Client(),
            run_store=RunStore(),
            lease_client=_LeaseClient(),
        ).run_action(
            action_kind="host.stop",
            profile_id="profile-1",
            host_lease_ref="lease-1",
            expected_host_state="ready",
            request_id="current-missing-authority",
        )

    assert runtime.stopped == 0
    assert repository.stopped == []


@pytest.mark.asyncio
async def test_forced_profile_drain_stops_idle_static_host_without_lease() -> None:
    repository = _Repository(_lease())

    async def no_leases():
        return []

    binding = SimpleNamespace(
        binding_ref="binding-static",
        host_launch_profile_ref=None,
    )
    repository.list_active_host_leases = no_leases

    async def get_binding(_profile_id):
        return binding

    repository.get_binding_for_profile = get_binding
    runtime = _Runtime()

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
    ).run(profile_id="profile-1", force=True)

    assert runtime.static_stops == 1
    assert result["actions"] == [
        {"hostBindingRef": "binding-static", "action": "static_host_stopped"}
    ]


@pytest.mark.asyncio
async def test_janitor_removes_label_owned_orphan_instead_of_repeating_report() -> None:
    repository = _Repository(_lease())
    runtime = _Runtime()
    runtime.managed_containers = ["orphan-host"]

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
    ).run()

    assert runtime.removed == ["orphan-host"]
    assert result["actions"] == [
        {
            "containerName": "orphan-host",
            "action": "orphan_container_removed",
            "providerLeaseReleased": False,
        }
    ]


@pytest.mark.asyncio
async def test_janitor_reloads_container_lease_before_orphan_removal() -> None:
    initial_lease = _lease()
    new_lease = _lease()
    new_lease.lease_id = "lease-created-after-scan"
    new_lease.container_name = "new-host"
    repository = _Repository(initial_lease)

    async def get_host_lease(lease_id):
        if lease_id == new_lease.lease_id:
            return new_lease
        return initial_lease if lease_id == initial_lease.lease_id else None

    repository.get_host_lease = get_host_lease
    runtime = _Runtime()
    runtime.managed_containers = ["new-host"]
    runtime.container_lease_refs["new-host"] = new_lease.lease_id

    result = await OmnigentOAuthHostJanitor(
        repository=repository, runtime=runtime, client=_Client(),
    ).run()

    assert result == {"status": "completed", "actions": [], "count": 0}
    assert runtime.removed == []


@pytest.mark.asyncio
async def test_stale_remediation_claim_conflict_prevents_cleanup() -> None:
    repository = _Repository(_lease(heartbeat_age=121))
    runtime = _Runtime()

    async def lost_claim(*_args, **_kwargs):
        repository.lease.last_heartbeat_at = datetime.now(UTC)
        return None

    repository.claim_host_lease_cleanup = lost_claim

    with pytest.raises(OmnigentOAuthHostError, match="changed concurrently"):
        await OmnigentOAuthHostJanitor(
            repository=repository,
            runtime=runtime,
            client=_Client(),
            heartbeat_timeout_seconds=90,
        ).run_action(
            action_kind="host_lease.reconcile_stale",
            profile_id="profile-1",
            host_lease_ref="lease-1",
            expected_host_state="ready",
            request_id="stale-race",
        )

    assert runtime.stopped == 0
    assert repository.stopped == []
