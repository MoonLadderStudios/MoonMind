from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[3] / "tools/run_omnigent_live_conformance.py"
    spec = importlib.util.spec_from_file_location("omnigent_live", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _matrix_fixtures():
    path = Path(__file__).parents[1] / "omnigent" / "test_remediation_matrix.py"
    spec = importlib.util.spec_from_file_location("remediation_matrix_fixtures", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_is_isolated_and_cleanup_preserves_volumes(tmp_path, monkeypatch):
    module = _module()
    calls = []

    class Result:
        returncode = 0

    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: calls.append(command) or Result())
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    runner.cleanup("stock")
    command = calls[0]
    assert command[:4] == ["docker", "compose", "--project-name", module.PROJECT]
    assert "down" in command
    assert "--volumes" not in command
    assert "-v" not in command


def test_static_restart_precedes_replay_and_cleanup_is_explicit(tmp_path, monkeypatch):
    module = _module()
    names = []
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    monkeypatch.setattr(runner, "run", lambda name, command: names.append(name))
    monkeypatch.setattr(runner, "scenario", lambda mode, phase=None: names.append(f"{mode}-{phase}"))
    monkeypatch.setattr(runner, "write_evidence", lambda mode, payload: None)
    monkeypatch.setattr(runner, "action", lambda scenario, action, **kw: {
        "ok": True, "workflowId": "w", "agentRunId": "a", "sessionId": "s",
        "one_first_message": True, "live_events": True, "final_snapshot": True,
        "resources": True, "workflow_detail": True, "secret_free": True,
        "durable_replay": True,
        "evidenceRefs": ["artifact://observed"],
    })
    runner.static()
    runner.cleanup("static")
    assert names == ["static-up", "static-execute", "static-restart", "static-replay", "static-cleanup"]


def test_stock_executes_every_route_and_derives_evidence(tmp_path, monkeypatch):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    actions = []
    monkeypatch.setattr(runner, "action", lambda scenario, action, **kw: actions.append(action) or {
        "ok": True, "protocolVersion": "v1", "hostArchitecture": "amd64",
        "agents": ["codex"], "capabilities": ["events"],
    })
    monkeypatch.setattr(runner, "run", lambda name, command: tmp_path / f"{name}.log")
    monkeypatch.setattr(runner, "scenario", lambda mode, phase=None: None)
    runner.stock({"server": "s@sha256:x", "host": "h@sha256:y"})
    evidence = json.loads((tmp_path / "stock-evidence.json").read_text())
    assert actions == [*module.STOCK_ROUTES, "inventory"]
    assert all(evidence["assertions"].values())


def test_ondemand_release_is_last_and_all_actions_execute(tmp_path, monkeypatch):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    actions = []
    monkeypatch.setattr(runner, "action", lambda scenario, action, **kw: actions.append(action) or {
        "ok": True, "exactProfileHost": True, "stateRemoved": True,
        "unrelatedResourcesSurvived": True, "credentialVolumePreserved": True,
        "available": True,
        "retryRecovered": True, "orphanRecovered": True,
        "state": {
            "leaseId": "l", "hostId": "h", "workflowId": "w",
            "agentRunId": "a", "sessionId": "s",
        },
        "evidenceRefs": [f"artifact://{action}"],
    })
    monkeypatch.setattr(runner, "scenario", lambda mode, phase=None: None)
    runner.ondemand()
    assert actions == list(module.ONDEMAND_ACTIONS)
    assert actions[-1] == "lease_released"


def test_product_uses_normal_create_and_release_last(tmp_path, monkeypatch):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    actions = []
    ids = {"workflowId": "w", "runId": "r", "stepId": "st", "bridgeId": "b",
           "hostId": "h", "sessionId": "s"}
    selection = {"agentKind": "external", "agentId": "omnigent",
                 "hostMode": "on_demand_docker", "providerProfileRef": "profile-safe"}
    acceptance = {
        "credentialGeneration": 7, "executionProfileRef": "execution-profile/v1",
        "policyVersion": "policy/v1",
        "effectiveLaunchSnapshotDigest": "sha256:" + "a" * 64,
        "serverImageDigest": "sha256:" + "b" * 64,
        "hostImageDigest": "sha256:" + "c" * 64,
        "caseOutcomes": {"normal-create-api": "passed"},
        "secretScan": {"status": "passed"},
        "evidence": {"artifacts": ["artifact://a"], "diagnostics": ["artifact://d"],
                     "history": ["artifact://h"], "screenshots": []},
        "cleanupAndRelease": {"runOwnedResourcesRemoved": True,
            "oauthVolumePreserved": True, "unrelatedResourcesPreserved": True,
            "profileReleasedLast": True},
    }
    def action(scenario, name, **inputs):
        actions.append(name)
        return {"ok": True, "state": {**ids, **acceptance, "selection": selection,
                "schemaVersions": {"create": "v1"}}, "evidenceRefs": [f"artifact://{name}"],
                "normalCreateApi": name == "workflow_created",
                "authoredIntentAndSnapshot": name == "authored_intent_persisted",
                "externalOmnigentCompilation": name == "request_compiled",
                "selectedAuthoritiesPreserved": name == "request_compiled",
                "temporalActivityRoute": name == "temporal_routed",
                "workflowDetailSse": name == "workflow_detail_streamed",
                "replayAfterRemoval": name == "workflow_detail_replayed",
                "releaseLast": name == "profile_released", "noFallback": True}
    monkeypatch.setattr(runner, "action", action)
    monkeypatch.setattr(runner, "scenario", lambda *args, **kwargs: None)
    runner.product()
    assert actions == list(module.PRODUCT_ACTIONS)
    assert actions[1] == "workflow_created"
    assert actions[-1] == "profile_released"
    evidence = json.loads((tmp_path / "product-evidence.json").read_text())
    assert all(evidence["assertions"].values())
    assert evidence["acceptance"] == acceptance


def test_product_rejects_incomplete_acceptance_report_fields(tmp_path, monkeypatch):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    ids = {"workflowId": "w", "runId": "r", "stepId": "st", "bridgeId": "b",
           "hostId": "h", "sessionId": "s"}
    monkeypatch.setattr(runner, "action", lambda *args, **kwargs: {
        "ok": True, "state": ids, "evidenceRefs": ["artifact://evidence"]
    })
    try:
        runner.product()
    except module.ConformanceContractError as exc:
        assert "lacks acceptance fields" in str(exc)
    else:
        raise AssertionError("incomplete product acceptance evidence was accepted")


def test_native_chat_protected_owns_exact_case_order_and_observed_outcomes(
    tmp_path, monkeypatch
):
    module = _module()
    runner = module.LiveRunner(
        output_dir=tmp_path,
        env={"MOONMIND_BUILD_COMMIT": "abc123", "MOONMIND_BUILD_ID": "build-1"},
    )
    actions = []
    digest = "a" * 64
    images = {
        "server": f"server@sha256:{digest}",
        "host": f"host@sha256:{digest}",
    }
    expected_identity = module.native_chat_deployment_identity(
        env={
            "MOONMIND_BUILD_COMMIT": "abc123",
            "OMNIGENT_IMAGE_REF": images["server"],
            "OMNIGENT_NATIVE_UI_IMAGE_REF": f"ui@sha256:{digest}",
            "OMNIGENT_HOST_IMAGE_REF": images["host"],
        }
    )
    expected_identity.update(
        {
            "moonmindBuild": "build-1",
            "hostArchitecture": f"linux/{module.platform.machine()}",
        }
    )

    def action(scenario, name, **inputs):
        actions.append(name)
        if name == "resolve-deployment-authority":
            return {
                "ok": True,
                "identities": expected_identity,
                "safeIdentities": {
                    "workflowRef": "workflow-ref",
                    "runRef": "run-ref",
                    "stepRef": "step-ref",
                    "agentRunRef": "agent-run-ref",
                    "bindingRef": "binding-ref",
                },
                "profilePolicyEvidence": {
                    key: f"file:///{key}.json"
                    for key in (
                        "profileRef",
                        "launchPolicyRef",
                        "effectiveLaunchSnapshotRef",
                        "providerProfileRef",
                    )
                },
                "evidenceRefs": ["file:///authority.json"],
            }
        return {
            "ok": True,
            "evidenceRefs": [f"file:///{name}.json"],
            "nativeChatOutcome": {
                "authorizationDecision": "not_applicable",
                "upstreamSideEffectCount": 0,
                "expectedUpstreamSideEffectCount": 0,
                "durableAfterCleanup": True,
                "evidenceKind": "artifact",
            },
        }

    monkeypatch.setattr(runner, "action", action)
    monkeypatch.setattr(
        runner,
        "_resolve_ref_bytes",
        lambda ref: json.dumps({"observedRef": ref}).encode(),
    )
    observations = runner.native_chat_protected(
        images=images, ui_image=f"ui@sha256:{digest}"
    )
    expected_actions = ["resolve-deployment-authority"] + [
        f"case.{scenario}.{case}"
        for scenario, cases in module.NATIVE_CHAT_REQUIRED_CASES.items()
        if module.NATIVE_CHAT_SCENARIO_LANES[scenario]
        == module.LANE_PROTECTED_LIVE
        for case in cases
    ] + [
        f"cleanup.{case}" for case in module.NATIVE_CHAT_REQUIRED_CLEANUP_CASES
    ]
    assert actions == expected_actions
    assert set(observations["scenarios"]) == {
        scenario
        for scenario, lane in module.NATIVE_CHAT_SCENARIO_LANES.items()
        if lane == module.LANE_PROTECTED_LIVE
    }


def test_native_chat_protected_rejects_bare_success_without_outcome(
    tmp_path, monkeypatch
):
    module = _module()
    runner = module.LiveRunner(
        output_dir=tmp_path,
        env={"MOONMIND_BUILD_COMMIT": "abc123", "MOONMIND_BUILD_ID": "build-1"},
    )
    digest = "a" * 64
    images = {
        "server": f"server@sha256:{digest}",
        "host": f"host@sha256:{digest}",
    }
    identity = module.native_chat_deployment_identity(
        env={
            "MOONMIND_BUILD_COMMIT": "abc123",
            "OMNIGENT_IMAGE_REF": images["server"],
            "OMNIGENT_NATIVE_UI_IMAGE_REF": f"ui@sha256:{digest}",
            "OMNIGENT_HOST_IMAGE_REF": images["host"],
        }
    )
    identity.update(
        {
            "moonmindBuild": "build-1",
            "hostArchitecture": f"linux/{module.platform.machine()}",
        }
    )
    calls = 0

    def action(_scenario, _name, **_inputs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "identities": identity,
                "safeIdentities": {"workflowRef": "w"},
                "profilePolicyEvidence": {
                    key: f"file:///{key}.json"
                    for key in (
                        "profileRef",
                        "launchPolicyRef",
                        "effectiveLaunchSnapshotRef",
                        "providerProfileRef",
                    )
                },
            }
        return {"ok": True, "evidenceRefs": ["file:///bare.json"]}

    monkeypatch.setattr(runner, "action", action)
    monkeypatch.setattr(runner, "_resolve_ref_bytes", lambda ref: b"{}")
    with pytest.raises(module.ConformanceContractError, match="observed case evidence"):
        runner.native_chat_protected(
            images=images, ui_image=f"ui@sha256:{digest}"
        )


def test_browser_executes_complete_release_rows_with_authority_chain(tmp_path, monkeypatch):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    actions = []
    authority = {name: f"{name}-value" for name in module.BROWSER_AUTHORITY_FIELDS}
    authority["hostCapability"] = "codex-native"
    authority["runtime"] = "external/omnigent"
    authority["providerProfileRef"] = "oauth-1"
    authority["executionProfileRef"] = "omnigent-codex-default"
    authority["launchPolicyRef"] = "on-demand-v1"
    authority["terminalState"] = "completed"
    authority["janitorState"] = "reconciled"
    observation = {
        "schemaVersion": "moonmind.omnigent.browser-observation/v1",
        "workflowId": "workflow-1",
        "selected": {
            "profileId": "oauth-1",
            "executionTargetRef": "omnigent-codex-default",
            "launchPolicyRef": "on-demand-v1",
        },
        "createRequest": {"payload": {"targetRuntime": "omnigent"}},
        "terminalUrl": "https://moonmind/workflows/workflow-1",
        "replayUrl": "https://moonmind/workflows/workflow-1",
        "replayComplete": True,
        "hostRemovedBeforeReplay": True,
    }
    monkeypatch.setattr(
        runner,
        "browser_observation",
        lambda row: (
            {
                "schemaVersion": "moonmind.omnigent.browser-observation/v1",
                "row": row,
                "admissionRejected": True,
                "createRequestCount": 0,
                "selected": observation["selected"],
                "admissionReason": (
                    "credential_readiness"
                    if row == "failed_credential_readiness_admission"
                    else "host_registration_readiness"
                ),
            }
            if row in {
                "failed_credential_readiness_admission",
                "failed_host_registration_readiness",
            }
            else {
                **observation,
                "row": row,
                "controlAction": "cancel_or_interrupt" if row == "active_cancellation_interruption" else None,
                "janitorReconciled": row == "partial_start_cleanup_janitor",
                "repositoryOutcome": (
                    "read_analysis" if row == "repository_read_analysis"
                    else "mutation_publication" if row == "repository_mutation_publication"
                    else None
                ),
            }
        ),
    )

    def action(scenario, name, **inputs):
        if scenario == "browser":
            actions.append(name)
        row_authority = dict(authority)
        if name == "active_cancellation_interruption":
            row_authority["terminalState"] = "cancelled"
        return {
            "ok": True,
            "row": name,
            "workflowId": "workflow-1",
            "browserOriginated": True,
            "normalCreateRequest": True,
            "workflowDetailTerminalReplay": True,
            "noFallback": True,
            "authorityChain": row_authority,
            "admissionAuthority": {"providerProfileRef": "oauth-1"},
            "repositoryMutationPublished": name == "repository_mutation_publication",
            "repositoryCommitSha": (
                "a" * 40 if name == "repository_mutation_publication" else None
            ),
            "publicationRef": (
                "https://github.example/pull/1"
                if name == "repository_mutation_publication" else None
            ),
            "staticHostRestarted": name == "static_restart_replay",
            "hostIdentityBeforeRestart": (
                "static-before" if name == "static_restart_replay" else None
            ),
            "hostIdentityAfterRestart": (
                "static-after" if name == "static_restart_replay" else None
            ),
            "_sourceRecords": [
                {"type": record_type, "_resolved": row_authority}
                for record_type in module.BROWSER_RECORD_ORDER
            ],
            "evidenceRefs": [f"artifact://{name}"],
        }

    monkeypatch.setattr(runner, "action", action)
    monkeypatch.setattr(runner, "scenario", lambda *args, **kwargs: None)
    runner.browser()
    assert actions == list(module.BROWSER_ROWS)
    evidence = json.loads((tmp_path / "browser-evidence.json").read_text())
    assert evidence["issue"] == "MoonLadderStudios/MoonMind#3508"
    assert set(evidence["rows"]) == set(module.BROWSER_ROWS)
    assert all(row["status"] == "passed" for row in evidence["rows"].values())


def test_browser_rejects_missing_authority_or_fallback_claim(tmp_path, monkeypatch):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    monkeypatch.setattr(runner, "browser_observation", lambda name: {
        "schemaVersion": "moonmind.omnigent.browser-observation/v1",
        "row": name,
        "workflowId": "workflow-1",
        "selected": {
            "profileId": "oauth-1",
            "executionTargetRef": "target",
            "launchPolicyRef": "policy",
        },
        "createRequest": {"payload": {"targetRuntime": "omnigent"}},
        "terminalUrl": "detail",
        "replayUrl": "detail",
    })
    monkeypatch.setattr(runner, "action", lambda scenario, name, **inputs: {
        "ok": True,
        "row": name,
        "browserOriginated": True,
        "normalCreateRequest": True,
        "workflowDetailTerminalReplay": True,
        "noFallback": name != module.BROWSER_ROWS[-1],
        "authorityChain": {},
        "browserControl": {
            "headless": True,
            "startPath": "/workflows/new",
            "submissionPath": "operator-frontend",
            "readinessObserved": True,
            "manualHostId": False,
        },
        "evidenceRefs": [f"artifact://{name}"],
    })
    try:
        runner.browser()
    except module.ConformanceContractError as exc:
        assert "authority chain" in str(exc) or "fallback" in str(exc)
    else:
        raise AssertionError("incomplete browser acceptance evidence was accepted")


def test_remediation_derives_every_catalog_row_from_observed_records(
    tmp_path, monkeypatch
):
    module = _module()
    fixtures = _matrix_fixtures()
    runner = module.LiveRunner(
        output_dir=tmp_path,
        env={
            "MOONMIND_OMNIGENT_LAUNCH_POLICY_VERSION": "launch-policy/v1",
            "MOONMIND_OMNIGENT_AGENT_PROFILE_VERSION": "agent-profile/v1",
            "MOONMIND_OMNIGENT_REMEDIATION_POLICY_VERSION": "remediation-policy/v1",
        },
    )
    images = {
        "server": "example/server@sha256:" + "1" * 64,
        "host": "example/host@sha256:" + "2" * 64,
    }
    row_by_id = {row.row_id: row for row in module.REMEDIATION_ROW_CATALOG}

    def action(scenario, name, **inputs):
        if scenario == "browser-setup":
            identity = fixtures._identity(name)
            return {
                "targetWorkflowId": identity["targetWorkflowId"],
                "targetRunId": identity["targetRunId"],
                "evidenceRefs": [f"artifact://setup/{name}"],
            }
        entry = fixtures._observed_row(name)
        records = []
        for manifest_record in entry["evidenceManifest"]:
            payload = fixtures._source_payload(entry, manifest_record["type"])
            path = tmp_path / manifest_record["ref"]
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(payload, sort_keys=True).encode()
            path.write_bytes(raw)
            records.append({
                "type": manifest_record["type"],
                "ref": path.resolve().as_uri(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "_resolved": payload,
                "_sizeBytes": len(raw),
            })
        ref_by_type = {record["type"]: record["ref"] for record in records}
        for record in records:
            lineage = record["_resolved"].get("lineage")
            if isinstance(lineage, dict):
                for field, owner in fixtures.REMEDIATION_LINEAGE_REF_RECORD_TYPES.items():
                    lineage[field] = ref_by_type[owner]
                raw = json.dumps(record["_resolved"], sort_keys=True).encode()
                Path(record["ref"].removeprefix("file://")).write_bytes(raw)
                record["sha256"] = hashlib.sha256(raw).hexdigest()
                record["_sizeBytes"] = len(raw)
        return {
            "_sourceRecords": records,
            "evidenceRefs": [f"artifact://row/{name}"],
        }

    def browser(row_id, **kwargs):
        row = row_by_id[row_id]
        identity = fixtures._identity(row_id)
        denied = row_id == "remediation.autonomous.rollout-gate-closed"
        return {
            "schemaVersion": "moonmind.operator-remediation-browser-observation/v1",
            "row": row_id,
            "selected": {
                "hostMode": (
                    "static_compose"
                    if row.host_modes[0] == "static"
                    else "on_demand_docker"
                )
            },
            "workflowId": None if denied else identity["remediationWorkflowId"],
            "targetWorkflowId": identity["targetWorkflowId"],
            "targetRunId": identity["targetRunId"],
            **{
                assertion: assertion != "normalCreateRequest" or not denied
                for assertion in module.REQUIRED_UI_JOURNEY_ASSERTIONS
            },
            **{
                marker: False
                for marker in module.PROHIBITED_UI_JOURNEY_MARKERS
            },
            "admissionRejected": denied,
            "admissionReason": "autonomous_rollout_gate" if denied else None,
        }

    monkeypatch.setattr(runner, "action", action)
    monkeypatch.setattr(runner, "browser_observation", browser)
    def scan(**kwargs):
        result = {
            channel: {
                "status": "passed",
                "evidenceRef": "",
                "sha256": "",
                "schemaVersion": "moonmind.retained-evidence-secret-scan/v1",
                "contentType": "application/json",
                "sizeBytes": 0,
                "generatedAt": "2026-08-13T00:00:00+00:00",
            }
            for channel in module.REQUIRED_REMEDIATION_RETAINED_CHANNELS
        }
        for channel, item in result.items():
            path = tmp_path / "secret-scans" / f"{channel}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps({
                "schemaVersion": item["schemaVersion"],
                "generatedAt": item["generatedAt"],
                "channel": channel,
                "status": "passed",
                "secretFindings": 0,
                "prohibitedAuthorityFindings": 0,
            }, sort_keys=True).encode()
            path.write_bytes(raw)
            item["evidenceRef"] = path.resolve().as_uri()
            item["sha256"] = hashlib.sha256(raw).hexdigest()
            item["sizeBytes"] = len(raw)
        return result

    monkeypatch.setattr(runner, "scan", scan)
    monkeypatch.setattr(runner, "scenario", lambda *args, **kwargs: None)

    runner.remediation(images)

    summary = json.loads((tmp_path / "remediation-evidence.json").read_text())
    assert summary["issue"] == "MoonLadderStudios/MoonMind#3626"
    assert set(summary["rows"]) == {
        row.row_id for row in module.REMEDIATION_ROW_CATALOG
    }
    assert len(summary["artifactRefs"]) == len(
        module.REQUIRED_REMEDIATION_EVIDENCE_KINDS
    )


def test_remediation_rejects_observation_for_a_different_browser_workflow() -> None:
    module = _module()

    with pytest.raises(
        module.ConformanceContractError,
        match="browser-created remediation workflow",
    ):
        module._validate_remediation_browser_lineage(
            row_id="remediation.branch.corrected-instruction-repair",
            browser_observation={
                "workflowId": "remediation-from-browser",
                "targetWorkflowId": "target-1",
                "targetRunId": "run-1",
            },
            lineage={
                "targetWorkflowId": "target-1",
                "targetRunId": "run-1",
                "remediationWorkflowId": "unrelated-remediation",
            },
            target_workflow_id="target-1",
            target_run_id="run-1",
        )


def test_cumulative_journey_requires_destroyed_source_and_distinct_attempts(
    tmp_path, monkeypatch
):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    attempts = [
        {"workspaceId": "w1", "leaseId": "l1", "hostId": "h1",
         "sessionId": "s1", "firstMessageId": "m1",
         "baseCheckpointRef": "artifact://workspace/C0"},
        {"workspaceId": "w2", "leaseId": "l2", "hostId": "h2",
         "sessionId": "s2", "firstMessageId": "m2",
         "baseCheckpointRef": "artifact://workspace/C1"},
    ]
    state = {
        "sourceWorkflowId": "source", "destinationWorkflowId": "destination",
        "continuationId": "continue-1", "profileRef": "profile-safe",
        "c0Ref": "artifact://workspace/C0", "c1Ref": "artifact://workspace/C1",
        "c2Ref": "artifact://workspace/C2", "attempts": attempts,
        "failureMatrix": {case: "passed" for case in module.FAILURE_CASES},
        "rollout": {
            "canary": True, "disableNewSelection": True, "rollback": True,
            "historicalReads": True, "workerVersionReplay": True,
        },
    }
    def action(scenario, name, **inputs):
        flags = {"noFallback": True, "state": state,
                 "evidenceRefs": [f"artifact://{name}"]}
        flags.update({
            "normalCreateApi": name == "workflow_created",
            "complete": name == "authored_state_persisted",
            "exactSelection": name == "request_compiled",
            "cumulative": name == "attempt_2_checkpoint_captured",
            "destroyed": name == "attempt_1_source_destroyed",
            "markerA": name == "checkpoint_c1_restored",
            "readOnly": name == "final_verification_passed",
            "sameDestination": name == "continuation_replayed",
            "noSideEffectReplay": name == "continuation_head_restored",
            "available": name == "workflow_detail_reloaded",
            "releaseLast": name == "profile_released",
        })
        return flags
    monkeypatch.setattr(runner, "action", action)
    monkeypatch.setattr(runner, "scenario", lambda *args, **kwargs: None)
    runner.cumulative()
    evidence = json.loads((tmp_path / "cumulative-evidence.json").read_text())
    assert evidence["identifiers"]["c2Ref"] == "artifact://workspace/C2"
    assert evidence["attempts"][0]["hostId"] != evidence["attempts"][1]["hostId"]
    assert all(evidence["assertions"].values())


def test_failure_matrix_executes_exact_issue_cases(tmp_path, monkeypatch):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    actions = []
    monkeypatch.setattr(runner, "action", lambda scenario, action, **kw: actions.append(action) or {
        "ok": True, "durableEvidence": {"injected": True, "lifecycleProjected": True,
        "terminalProjected": True, "redacted": True, "noFallback": True},
        "evidenceRefs": [f"artifact://{action}"],
    })
    monkeypatch.setattr(runner, "scenario", lambda mode, phase=None: None)
    runner.failures()
    evidence = json.loads((tmp_path / "failures-evidence.json").read_text())
    assert actions == list(module.FAILURE_CASES)
    assert set(evidence["failureCases"]) == set(module.FAILURE_CASES)


def test_scan_rejects_secret_like_live_evidence(tmp_path):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    log = tmp_path / "provider.log"
    log.write_text("authorization=do-not-publish")
    runner.logs.append(log)
    try:
        runner.scan()
    except module.ConformanceContractError as exc:
        assert "secret-like material" in str(exc)
    else:
        raise AssertionError("secret-like evidence was accepted")


def test_scan_rejects_retained_raw_authority(tmp_path):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    log = tmp_path / "provider.log"
    log.write_text('{"dockerSocket":"/var/run/docker.sock"}')
    runner.logs.append(log)
    try:
        runner.scan()
    except module.ConformanceContractError as exc:
        assert "prohibited raw authority" in str(exc)
    else:
        raise AssertionError("prohibited raw authority was accepted")


def test_each_mode_selects_a_distinct_provider_node():
    module = _module()
    assert set(module.SCENARIOS) == set(module.LIVE_CASES)
    assert len(set(module.SCENARIOS.values())) == len(module.SCENARIOS)


def test_static_replay_is_not_pytest_collection_placeholder():
    module = _module()
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "--collect-only" not in source


def test_every_mode_has_dedicated_scenario_evidence_channel():
    module = _module()
    assert set(module.SCENARIO_EVIDENCE_ENV) == set(module.LIVE_CASES)
    assert len(set(module.SCENARIO_EVIDENCE_ENV.values())) == len(module.LIVE_CASES)


def test_scan_requires_each_evidence_channel(tmp_path):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    try:
        runner.scan()
    except module.ConformanceContractError as exc:
        assert "evidence was not collected" in str(exc)
    else:
        raise AssertionError("missing evidence channels were accepted")


def test_action_rejects_boolean_attestation_without_evidence(tmp_path, monkeypatch):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={"MOONMIND_OMNIGENT_ACTION_COMMAND": "adapter"})
    class Result:
        returncode = 0
        stdout = '{"ok":true,"retryRecovered":true}'
        stderr = ""
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    try:
        runner.action("ondemand", "partial_start_retry")
    except module.ConformanceContractError as exc:
        assert "durable evidence refs" in str(exc)
    else:
        raise AssertionError("bare boolean attestation was accepted")


def _action_evidence(tmp_path, scenario, action, identifiers=None, source_records=None):
    path = tmp_path / f"{scenario}-{action}.json"
    path.write_text(json.dumps({
        "schemaVersion": "moonmind.omnigent.action-evidence/v1",
        "scenario": scenario, "action": action, "observed": True,
        "identifiers": identifiers or {},
        "sourceRecords": source_records or [],
    }))
    return path.as_uri()


def test_action_resolves_and_validates_evidence_content(tmp_path, monkeypatch):
    module = _module()
    ref = _action_evidence(tmp_path, "static", "execute", {"workflowId": "w"})
    runner = module.LiveRunner(output_dir=tmp_path, env={"MOONMIND_OMNIGENT_ACTION_COMMAND": "adapter"})
    class Result:
        returncode = 0
        stdout = json.dumps({"ok": True, "workflowId": "w", "evidenceRefs": [ref]})
        stderr = ""
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    assert runner.action("static", "execute")["workflowId"] == "w"


def test_action_rejects_mismatched_or_unreachable_evidence(tmp_path, monkeypatch):
    module = _module()
    bad_ref = _action_evidence(tmp_path, "static", "replay", {"workflowId": "other"})
    responses = [
        {"ok": True, "workflowId": "w", "evidenceRefs": [bad_ref]},
        {"ok": True, "evidenceRefs": [(tmp_path / "missing.json").as_uri()]},
    ]
    runner = module.LiveRunner(output_dir=tmp_path, env={"MOONMIND_OMNIGENT_ACTION_COMMAND": "adapter"})
    class Result:
        returncode = 0
        stderr = ""
        @property
        def stdout(self):
            return json.dumps(responses.pop(0))
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    for action in ("execute", "replay"):
        try:
            runner.action("static", action)
        except module.ConformanceContractError as exc:
            if action == "execute":
                assert "evidence did not describe the observed action" in str(exc)
            else:
                assert "unreachable or malformed" in str(exc)
        else:
            raise AssertionError("invalid durable evidence was accepted")


def test_native_chat_action_binds_outcome_to_resolved_evidence(tmp_path, monkeypatch):
    module = _module()
    ref = _action_evidence(tmp_path, "native-chat", "launch-native-ui")
    evidence_path = Path(ref.removeprefix("file://"))
    evidence = json.loads(evidence_path.read_text())
    evidence["nativeChatOutcome"] = {"loaded": False}
    evidence_path.write_text(json.dumps(evidence))
    runner = module.LiveRunner(
        output_dir=tmp_path,
        env={"MOONMIND_OMNIGENT_ACTION_COMMAND": "adapter"},
    )

    class Result:
        returncode = 0
        stdout = json.dumps(
            {
                "ok": True,
                "nativeChatOutcome": {"loaded": True},
                "evidenceRefs": [ref],
            }
        )
        stderr = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(module.ConformanceContractError, match="outcome is not bound"):
        runner.action("native-chat", "launch-native-ui")


def test_product_action_binds_all_lifecycle_ids_to_evidence(tmp_path, monkeypatch):
    module = _module()
    ids = {"workflowId": "w", "runId": "r", "stepId": "s", "bridgeId": "b"}
    ref = _action_evidence(tmp_path, "product", "runtime_catalog_loaded", {
        **ids, "bridgeId": "different",
    }, source_records=[])
    evidence = json.loads(Path(ref.removeprefix("file://")).read_text())
    record_path = tmp_path / "runtime-catalog.json"
    record_path.write_text('{"catalog":true}')
    evidence["sourceRecords"] = [{
        "type": "runtimeCatalog", "ref": record_path.as_uri(),
        "sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
    }]
    Path(ref.removeprefix("file://")).write_text(json.dumps(evidence))
    runner = module.LiveRunner(output_dir=tmp_path, env={"MOONMIND_OMNIGENT_ACTION_COMMAND": "adapter"})
    class Result:
        returncode = 0
        stdout = json.dumps({"ok": True, "state": ids, "evidenceRefs": [ref]})
        stderr = ""
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    try:
        runner.action("product", "runtime_catalog_loaded")
    except module.ConformanceContractError as exc:
        assert "evidence identifiers do not match" in str(exc)
    else:
        raise AssertionError("mismatched product identifiers were accepted")


def test_product_action_resolves_and_hashes_source_records(tmp_path, monkeypatch):
    module = _module()
    record_path = tmp_path / "create-request.json"
    record_path.write_text('{"request":true}')
    records = [
        {"type": record_type, "ref": record_path.as_uri(), "sha256": "0" * 64}
        for record_type in module.PRODUCT_RECORD_TYPES["workflow_created"]
    ]
    ref = _action_evidence(tmp_path, "product", "workflow_created", source_records=records)
    runner = module.LiveRunner(output_dir=tmp_path, env={"MOONMIND_OMNIGENT_ACTION_COMMAND": "adapter"})
    class Result:
        returncode = 0
        stdout = json.dumps({"ok": True, "evidenceRefs": [ref]})
        stderr = ""
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    try:
        runner.action("product", "workflow_created")
    except module.ConformanceContractError as exc:
        assert "source record digest does not match" in str(exc)
    else:
        raise AssertionError("unverified source record digest was accepted")


def test_ondemand_threads_state_between_actions(tmp_path, monkeypatch):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    seen = []
    state = {"leaseId": "l", "hostId": "h", "workflowId": "w", "agentRunId": "a", "sessionId": "s"}
    def action(scenario, name, **inputs):
        seen.append(dict(inputs))
        return {"ok": True, "state": state, "evidenceRefs": [f"artifact://{name}"],
                "exactProfileHost": True, "retryRecovered": True, "orphanRecovered": True,
                "stateRemoved": True, "unrelatedResourcesSurvived": True,
                "credentialVolumePreserved": True, "available": True}
    monkeypatch.setattr(runner, "action", action)
    monkeypatch.setattr(runner, "scenario", lambda *args, **kwargs: None)
    runner.ondemand()
    assert seen[0] == {}
    assert all(call == state for call in seen[1:])


def test_product_rejects_semantic_attestation_without_source_records(tmp_path, monkeypatch):
    module = _module()
    ref = _action_evidence(tmp_path, "product", "workflow_created")
    runner = module.LiveRunner(
        output_dir=tmp_path,
        env={"MOONMIND_OMNIGENT_ACTION_COMMAND": "adapter"},
    )
    class Result:
        returncode = 0
        stdout = json.dumps({"ok": True, "evidenceRefs": [ref]})
        stderr = ""
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    try:
        runner.action("product", "workflow_created")
    except module.ConformanceContractError as exc:
        assert "independently resolved source records" in str(exc)
    else:
        raise AssertionError("semantic product attestation was accepted")
