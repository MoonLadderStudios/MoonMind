from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    path = Path(__file__).parents[3] / "tools/run_omnigent_live_conformance.py"
    spec = importlib.util.spec_from_file_location("omnigent_live", path)
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
                 "hostMode": "on_demand_docker"}
    def action(scenario, name, **inputs):
        actions.append(name)
        return {"ok": True, "state": {**ids, "selection": selection,
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


def _action_evidence(tmp_path, scenario, action, identifiers=None):
    path = tmp_path / f"{scenario}-{action}.json"
    path.write_text(json.dumps({
        "schemaVersion": "moonmind.omnigent.action-evidence/v1",
        "scenario": scenario, "action": action, "observed": True,
        "identifiers": identifiers or {},
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


def test_repository_backend_persists_static_replay_and_rejects_wrong_ids(tmp_path):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={
        "MOONMIND_OMNIGENT_ACTION_COMMAND": f"{module.sys.executable} {module.REPO_ROOT / 'tools/omnigent_live_action.py'}"
    })
    executed = runner.action("static", "execute")
    ids = {key: executed[key] for key in ("workflowId", "agentRunId", "sessionId")}
    assert runner.action("static", "replay", **ids)["durable_replay"] is True
    try:
        runner.action("static", "replay", **{**ids, "workflowId": "wrong"})
    except RuntimeError:
        pass
    else:
        raise AssertionError("backend accepted replay for another workflow")


def test_repository_backend_enforces_lifecycle_and_failure_projection(tmp_path):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={
        "MOONMIND_OMNIGENT_ACTION_COMMAND": f"{module.sys.executable} {module.REPO_ROOT / 'tools/omnigent_live_action.py'}"
    })
    try:
        runner.action("ondemand", "host_launched")
    except RuntimeError:
        pass
    else:
        raise AssertionError("backend accepted host launch before lease")
    state = {}
    for action in module.ONDEMAND_ACTIONS:
        result = runner.action("ondemand", action, **state)
        state.update(result["state"])
    assert all(state.get(key) for key in ("leaseId", "hostId", "workflowId", "agentRunId", "sessionId"))
    for case in module.FAILURE_CASES:
        durable = runner.action("failures", case)["durableEvidence"]
        assert all(durable.values())


def test_repository_backend_enforces_product_create_lifecycle(tmp_path):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={
        "MOONMIND_OMNIGENT_ACTION_COMMAND": f"{module.sys.executable} {module.REPO_ROOT / 'tools/omnigent_live_action.py'}"
    })
    state = {}
    for action in module.PRODUCT_ACTIONS:
        result = runner.action("product", action, **state)
        state.update(result["state"])
        assert result["noFallback"] is True
    assert state["selection"]["agentKind"] == "external"
    assert state["selection"]["agentId"] == "omnigent"
