"""MM-995 live Omnigent smoke checks.

These tests are provider verification only. They require a real disposable
Omnigent server and are intentionally excluded from credential-free CI.
Source issue traceability: MM-981 -> MM-995.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import OmnigentBridgeSession
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    BridgeSessionEventRequest,
    OmnigentBridgeSessionProxy,
)
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.settings import is_omnigent_enabled
from moonmind.omnigent.remediation_matrix import (
    REMEDIATION_MATRIX_VERSION,
    REMEDIATION_EVIDENCE_IDENTITY_FIELDS,
    REMEDIATION_ROW_CATALOG,
    REMEDIATION_SOURCE_RECORD_SCHEMAS,
    REQUIRED_REMEDIATION_EVIDENCE_KINDS,
    validate_remediation_evidence_artifact,
)
from moonmind.omnigent.workflow_chat_acceptance import (
    REQUIRED_WORKFLOW_CHAT_ROWS,
    WORKFLOW_CHAT_ACCEPTANCE_ISSUE,
    validate_workflow_chat_acceptance_manifest,
    workflow_chat_combinations,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.provider_verification,
    pytest.mark.requires_credentials,
]

_SUCCESS_STATUSES = {"completed", "succeeded"}
_TERMINAL_STATUSES = _SUCCESS_STATUSES | {"failed", "canceled", "timed_out"}
_SMOKE_PROMPT = "Reply with: MM-995 live bridge smoke complete"
_REQUIRED_STOCK_ROUTES = {
    "agents", "hosts", "session.create", "session.get", "event.post",
    "events.stream", "elicitation.resolve", "interrupt", "stop",
    "changed-files", "workspace.files", "workspace.content",
    "workspace.diff", "session.files", "session.content", "terminal.snapshot",
}
_REQUIRED_FAILURES = {
    "create_invalid_selector", "create_duplicate_submit",
    "create_persistence_failure", "compile_runtime_shape_mismatch",
    "capability_authority_mismatch", "workspace_stale_version",
    "workspace_wrong_parent", "workspace_concurrent_advance",
    "checkpoint_capture_failure", "restore_missing", "restore_corrupt",
    "restore_unauthorized", "restore_digest_mismatch",
    "stale_runtime_catalog", "no_eligible_profile", "disconnected_profile",
    "profile_lease_busy", "bounded_lease_timeout", "disabled_execution_profile",
    "incompatible_policy", "invalid_workspace", "escaped_workspace",
    "docker_unavailable", "worker_unavailable", "host_image_pull_failure",
    "host_image_start_failure", "network_policy_failure", "egress_policy_failure",
    "mount_policy_failure", "invalid_oauth", "registration_timeout",
    "codex_native_mismatch", "bridge_server_auth_failure",
    "bridge_session_authorization_failure", "server_unavailable",
    "ambiguous_first_message_reconciliation", "active_session_disconnect",
    "resource_route_unavailable", "operator_cancelled",
    "artifact_persistence_failure", "cleanup_failure", "profile_release_failure",
}
_ONDEMAND_ORDER = (
    "lease_acquired", "host_launched", "preflight_ready", "session_bound",
    "executed", "resources_harvested", "partial_start_retry", "janitor_recovery",
    "host_removed",
    "workflow_detail_reloaded", "lease_released",
)


def _scenario_evidence(env_name: str) -> dict[str, object]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        pytest.fail(f"required scenario evidence is unset: {env_name}")
    path = Path(raw)
    if not path.is_file():
        pytest.fail(f"scenario evidence does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("schemaVersion") == "moonmind.omnigent.live-evidence/v1"
    return payload


def _assert_passed(payload: dict[str, object], names: set[str]) -> None:
    assertions = payload.get("assertions")
    assert isinstance(assertions, dict)
    missing = sorted(name for name in names if assertions.get(name) is not True)
    assert not missing, f"scenario assertions not proved: {missing}"


def _live_env() -> dict[str, str]:
    required = {
        "OMNIGENT_ENABLED": os.environ.get("OMNIGENT_ENABLED", ""),
        "OMNIGENT_SERVER_URL": os.environ.get("OMNIGENT_SERVER_URL", ""),
        "OMNIGENT_API_TOKEN": os.environ.get("OMNIGENT_API_TOKEN", ""),
        "OMNIGENT_DEFAULT_AGENT_NAME": os.environ.get(
            "OMNIGENT_DEFAULT_AGENT_NAME",
            "",
        ),
    }
    missing = [key for key, value in required.items() if not value.strip()]
    if missing:
        message = (
            "live Omnigent smoke requires provider credentials: "
            + ", ".join(missing)
        )
        if os.environ.get("MOONMIND_OMNIGENT_STRICT_LIVE") == "1":
            pytest.fail(message)
        pytest.skip(message)
    if not is_omnigent_enabled(env=required):
        if os.environ.get("MOONMIND_OMNIGENT_STRICT_LIVE") == "1":
            pytest.fail("live Omnigent smoke requires OMNIGENT_ENABLED=true")
        pytest.skip("live Omnigent smoke requires OMNIGENT_ENABLED=true")
    return required


def _require_mode(expected: str) -> None:
    actual = os.environ.get("MOONMIND_OMNIGENT_LIVE_MODE", "")
    if actual != expected:
        pytest.fail(f"scenario {expected!r} invoked with live mode {actual!r}")


def _message_event(text: str) -> BridgeSessionEventRequest:
    return BridgeSessionEventRequest(
        type="message",
        data={
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    )


def _event_status(event: dict[str, object]) -> str:
    session = event.get("session")
    if isinstance(session, dict):
        return str(session.get("status") or "").strip().lower()
    return ""


@pytest_asyncio.fixture
async def bridge_store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge-smoke.db")
    async with engine.begin() as conn:
        await conn.run_sync(OmnigentBridgeSession.__table__.create)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentBridgeSessionStore(session_maker)
    finally:
        await engine.dispose()


async def test_live_omnigent_bridge_smoke_disposable_managed_session(
    bridge_store: OmnigentBridgeSessionStore,
) -> None:
    env = _live_env()
    client = OmnigentHttpClient(
        base_url=env["OMNIGENT_SERVER_URL"],
        api_token=env["OMNIGENT_API_TOKEN"],
    )
    proxy = OmnigentBridgeSessionProxy(
        run_store=bridge_store,
        client=client,
        default_agent_name=env["OMNIGENT_DEFAULT_AGENT_NAME"],
    )
    binding = BridgePrincipalBinding(
        workflow_id="mm-995-live-smoke",
        correlation_id="mm-995-live-smoke",
        idempotency_key="mm-995-live-smoke",
        agent_run_id="ar-mm-995-live-smoke",
    )

    agents = await proxy.list_agents()
    created = await proxy.create_session(
        request=BridgeSessionCreateRequest(
            title="MM-995 live bridge smoke",
            host_type="managed",
        ),
        binding=binding,
    )
    posted = await proxy.post_event(
        session_id=created["id"],
        event=_message_event(_SMOKE_PROMPT),
    )
    stream_events = []
    async for event in client.stream_events(created["id"]):
        stream_events.append(event)
        if event.get("type") == "response.completed":
            break
        if _event_status(event) in _TERMINAL_STATUSES:
            break
    final_snapshot = await proxy.get_session(created["id"])
    harvested = await proxy.harvest_session(created["id"])
    reused = await proxy.create_session(
        request=BridgeSessionCreateRequest(
            title="MM-995 live bridge smoke",
            host_type="managed",
        ),
        binding=binding,
    )

    assert agents
    assert created["id"]
    assert posted
    assert stream_events
    assert final_snapshot["id"] == created["id"]
    assert str(final_snapshot.get("status") or "").lower() in _SUCCESS_STATUSES
    assert "resources" in harvested
    assert reused["id"] == created["id"]
    assert reused["moonmind"]["reused"] is True


async def test_live_stock_proxy_compatibility_profile(bridge_store) -> None:
    _require_mode("stock")
    evidence = _scenario_evidence("MOONMIND_OMNIGENT_STOCK_EVIDENCE")
    _assert_passed(evidence, _REQUIRED_STOCK_ROUTES)
    images = evidence.get("images")
    assert isinstance(images, dict)
    assert all("@sha256:" in str(images.get(name, "")) for name in ("server", "host"))
    assert evidence.get("hostSource") == "published-stock-image"
    assert evidence.get("moonmindHostPatch") is False
    assert evidence.get("protocolVersion") and evidence.get("hostArchitecture")
    assert evidence.get("advertisedAgents") and evidence.get("advertisedCapabilities")
    await test_live_omnigent_bridge_smoke_disposable_managed_session(bridge_store)


async def test_live_product_create_api_journey(bridge_store) -> None:
    _require_mode("product")
    evidence = _scenario_evidence("MOONMIND_OMNIGENT_PRODUCT_EVIDENCE")
    assert evidence.get("issue") == "MoonLadderStudios/MoonMind#3456"
    _assert_passed(evidence, {
        "normal_create_api", "authored_intent_and_snapshot",
        "external_omnigent_compilation", "selected_profile_policy_workspace",
        "real_temporal_activity_route", "workflow_detail_sse", "release_last",
        "replay_after_host_removal", "no_fallback",
    })
    selection = evidence.get("selection")
    assert isinstance(selection, dict)
    assert selection.get("agentKind") == "external"
    assert selection.get("agentId") == "omnigent"
    assert selection.get("hostMode") == "on_demand_docker"
    assert evidence.get("schemaVersions")


async def test_live_browser_release_matrix(bridge_store) -> None:
    _require_mode("browser")
    evidence = _scenario_evidence("MOONMIND_OMNIGENT_BROWSER_EVIDENCE")
    assert evidence.get("issue") == "MoonLadderStudios/MoonMind#3508"
    assert evidence.get("parentIssue") == "MoonLadderStudios/MoonMind#3448"
    assert evidence.get("entrypoint") == "/workflows/new"
    rows = evidence.get("rows")
    assert isinstance(rows, dict)
    assert set(rows) == {
        "static_profile_bound", "static_restart_replay",
        "on_demand_policy_selected", "repository_read_analysis",
        "repository_mutation_publication",
        "failed_credential_readiness_admission",
        "failed_host_registration_readiness",
        "active_cancellation_interruption",
        "partial_start_cleanup_janitor",
    }
    admission_rows = {
        "failed_credential_readiness_admission",
        "failed_host_registration_readiness",
    }
    for name, row in rows.items():
        assert row.get("status") == "passed"
        _assert_passed(
            row,
            {
                "browser_originated", "normal_create_request_rejected",
                "distinct_admission_reason", "no_fallback",
            }
            if name in admission_rows
            else {
                "browser_originated", "normal_create_request",
                "workflow_detail_terminal_replay", "no_fallback",
            },
        )
        authority = row.get("authorityChain")
        assert isinstance(authority, dict)
        if name in admission_rows:
            assert set(authority) == {"providerProfileRef"}
            assert authority.get("providerProfileRef")
            continue
        assert authority.get("hostCapability") == "codex-native"
        assert authority.get("runtime") == "external/omnigent"
        observation = row.get("browserObservation")
        assert isinstance(observation, dict)
        assert observation.get("schemaVersion") == "moonmind.omnigent.browser-observation/v1"
        assert observation.get("startPath", "/workflows/new") == "/workflows/new"
        assert observation.get("workflowId")


async def test_live_native_workflow_chat_release_matrix(bridge_store) -> None:
    """Validate the protected browser-to-stock-host #3642 rollout artifact."""

    del bridge_store  # provider marker/fixture keep this on the live test lane
    _require_mode("workflow_chat")
    raw = os.environ.get("MOONMIND_OMNIGENT_WORKFLOW_CHAT_EVIDENCE", "").strip()
    if not raw:
        pytest.fail(
            "required scenario evidence is unset: "
            "MOONMIND_OMNIGENT_WORKFLOW_CHAT_EVIDENCE"
        )
    path = Path(raw).resolve()
    if not path.is_file():
        pytest.fail(f"scenario evidence does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_commit = os.environ.get("MOONMIND_OMNIGENT_SOURCE_COMMIT", "").strip()
    if not expected_commit:
        pytest.fail(
            "MOONMIND_OMNIGENT_SOURCE_COMMIT is required for the release gate"
        )
    validate_workflow_chat_acceptance_manifest(
        payload,
        evidence_root=path.parent,
        expected_commit=expected_commit,
    )
    assert payload.get("issue") == WORKFLOW_CHAT_ACCEPTANCE_ISSUE
    combinations = payload.get("combinations") or {}
    assert set(combinations) == set(workflow_chat_combinations())
    for combination_id, combination in workflow_chat_combinations().items():
        entry = combinations[combination_id]
        if not combination.native_chat_claimed:
            assert entry.get("status") == "unsupported"
            assert entry.get("unsupportedReason")
            continue
        assert entry.get("status") == "passed"
        assert set(entry.get("rows") or {}) == set(REQUIRED_WORKFLOW_CHAT_ROWS)


async def test_live_cumulative_remediation_journey(bridge_store) -> None:
    _require_mode("cumulative")
    evidence = _scenario_evidence("MOONMIND_OMNIGENT_CUMULATIVE_EVIDENCE")
    assert evidence.get("issue") == "MoonLadderStudios/MoonMind#3480"
    assert evidence.get("parentIssue") == "MoonLadderStudios/MoonMind#3471"
    assert evidence.get("acceptanceIssue") == "MoonLadderStudios/MoonMind#3456"
    _assert_passed(evidence, {
        "normal_create_api", "authored_state_persisted",
        "external_omnigent_compilation", "c0_c1_c2_head_transitions",
        "source_destroyed_before_restore", "marker_a_restored_before_marker_b",
        "verification_read_only", "continuation_idempotent",
        "prior_side_effects_not_replayed", "workflow_detail_after_removal",
        "profile_released_last", "no_fallback",
    })
    attempts = evidence.get("attempts")
    assert isinstance(attempts, list) and len(attempts) >= 2
    assert attempts[0]["baseCheckpointRef"] == evidence["identifiers"]["c0Ref"]
    assert attempts[1]["baseCheckpointRef"] == evidence["identifiers"]["c1Ref"]
    assert evidence.get("failureMatrix")
    rollout = evidence.get("rollout")
    assert isinstance(rollout, dict)
    assert all(rollout.get(key) is True for key in (
        "canary", "disableNewSelection", "rollback", "historicalReads",
        "workerVersionReplay",
    ))


async def test_live_operator_remediation_release_matrix(bridge_store) -> None:
    _require_mode("remediation")
    evidence = _scenario_evidence(
        "MOONMIND_OMNIGENT_REMEDIATION_MATRIX_EVIDENCE"
    )
    assert evidence.get("issue") == "MoonLadderStudios/MoonMind#3626"
    assert evidence.get("matrixVersion") == REMEDIATION_MATRIX_VERSION
    assert evidence.get("entrypoint") == "workflow-detail.remediate.normal-create"
    rows = evidence.get("rows")
    assert isinstance(rows, dict)
    assert set(rows) == {row.row_id for row in REMEDIATION_ROW_CATALOG}
    for row in rows.values():
        browser = row.get("browserObservation")
        assert isinstance(browser, dict)
        assert browser.get("browserOriginated") is True
        assert browser.get("importedPinnedRemediationDraft") is True
        assert browser.get("validatedPolicyProfileFields") is True
        assert browser.get("workflowDetailFollowThrough") is True
        assert set(row.get("sourceRecordTypes") or ()) == {
            "scenarioObservation",
            "browserTrace",
            "authoredRequest",
            "immutableInputSnapshot",
            "workflowLineage",
            "contextEvidence",
            "profilePolicyAuthority",
            "egressAttestation",
            "approvalDecision",
            "actionResult",
            "verificationResult",
            "publicationOutcome",
            "cleanupOutcome",
            "temporalHistory",
            "sideEffectAudit",
            "retainedEvidenceScan",
        }
    artifact_refs = evidence.get("artifactRefs")
    assert isinstance(artifact_refs, list)
    assert len(artifact_refs) == len(REQUIRED_REMEDIATION_EVIDENCE_KINDS)
    observed_rows: set[str] = set()
    for ref in artifact_refs:
        assert isinstance(ref, str) and ref.startswith("file://")
        artifact_path = Path(ref.removeprefix("file://"))
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        for observed_row in payload["rows"]:
            for record in observed_row["evidenceManifest"]:
                parsed = urlparse(record["ref"])
                source_path = (
                    Path(unquote(parsed.path))
                    if parsed.scheme == "file"
                    else artifact_path.parent / record["ref"]
                )
                source = json.loads(source_path.read_text(encoding="utf-8"))
                assert source["schemaVersion"] == REMEDIATION_SOURCE_RECORD_SCHEMAS[
                    record["type"]
                ]
                assert source["row"] == observed_row["row"]
                assert all(
                    source["identity"].get(field)
                    for field in REMEDIATION_EVIDENCE_IDENTITY_FIELDS
                )
        first = payload["rows"][0]
        kind, artifact_rows = validate_remediation_evidence_artifact(
            payload,
            expected_kind=payload["kind"],
            images=first["images"],
            architectures=evidence["architectures"],
            profile_version=first["profileVersion"],
            profile_sha256=first["profileSha256"],
            policy_version=first["launchPolicyVersion"],
            agent_profile_version=first["agentProfileVersion"],
            remediation_policy_version=first["remediationPolicyVersion"],
            evidence_document_path=artifact_path,
        )
        assert kind in REQUIRED_REMEDIATION_EVIDENCE_KINDS
        observed_rows.update(artifact_rows)
    assert observed_rows == {row.row_id for row in REMEDIATION_ROW_CATALOG}


async def test_live_static_workflow_detail_restart_replay(bridge_store) -> None:
    _require_mode("static")
    evidence = _scenario_evidence("MOONMIND_OMNIGENT_STATIC_EVIDENCE")
    phase = os.environ.get("MOONMIND_OMNIGENT_STATIC_PHASE")
    assert phase in {"execute", "replay"}
    _assert_passed(evidence, {
        "one_first_message", "live_events", "final_snapshot", "resources",
        "workflow_detail", "secret_free",
    })
    assert evidence.get("workflowId") and evidence.get("agentRunId") and evidence.get("sessionId")
    if phase == "execute":
        _assert_passed(evidence, {"workflow_created_through_static_profile"})
    else:
        _assert_passed(evidence, {"services_restarted", "same_identifiers_reloaded", "durable_replay"})


async def test_live_ondemand_oauth_lifecycle_and_cleanup(bridge_store) -> None:
    _require_mode("ondemand")
    evidence = _scenario_evidence("MOONMIND_OMNIGENT_ONDEMAND_EVIDENCE")
    events = evidence.get("events")
    assert isinstance(events, list)
    positions = [events.index(name) for name in _ONDEMAND_ORDER]
    assert positions == sorted(positions)
    assert positions[-1] == len(events) - 1, "lease release must be the final owned side effect"
    _assert_passed(evidence, {
        "exact_profile_host", "partial_start_retry", "janitor_recovery",
        "state_removed_per_policy", "unrelated_resources_survived",
        "credential_volume_preserved", "workflow_detail_available_after_removal",
    })


async def test_live_failure_matrix_and_durable_evidence(bridge_store) -> None:
    _require_mode("failures")
    evidence = _scenario_evidence("MOONMIND_OMNIGENT_FAILURE_EVIDENCE")
    cases = evidence.get("failureCases")
    assert isinstance(cases, dict)
    assert set(cases) == _REQUIRED_FAILURES
    for name, result in cases.items():
        assert isinstance(result, dict), name
        assert result.get("injected") is True, name
        assert result.get("lifecycleProjected") is True, name
        assert result.get("terminalProjected") is True, name
        assert result.get("redacted") is True, name
        assert result.get("noFallback") is True, name
