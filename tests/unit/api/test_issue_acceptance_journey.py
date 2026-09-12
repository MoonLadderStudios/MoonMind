"""Production preset -> portable evidence -> gate -> continuation -> issue effect.

Only the external GitHub HTTP boundary is replaced. Commit/tree responses come
from real Git refs and acceptance evidence comes from executed fixture tests.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import subprocess
from urllib.parse import unquote

import httpx
import pytest

from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.remediation_loop import (
    RemediationLoopSpec,
    RemediationLoopState,
    decide_remediation_continuation,
)
from moonmind.workflows.temporal.story_output_tools import (
    update_github_issue_status,
    update_jira_issue_status,
)
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.api.test_presets_service import template_db
from tests.unit.workflows.skills import test_acceptance_contract as acceptance_helpers

ROOT = acceptance_helpers.ROOT
candidate = acceptance_helpers.candidate
git = acceptance_helpers.git
portable = acceptance_helpers.portable


class ArtifactStore:
    def __init__(self, payloads):
        self.payloads = payloads
        self.reads = []

    async def read(self, *, artifact_id, **_):
        self.reads.append(artifact_id)
        return None, json.dumps(self.payloads[artifact_id]).encode()


@pytest.fixture
def github_boundary(candidate, monkeypatch):
    repo, _, _ = candidate
    state = {
        "number": 1,
        "title": "Return 42",
        "body": "Return 42.",
        "state": "open",
        "labels": [],
        "html_url": "https://github.com/example/repo/issues/1",
    }
    requests = []

    def handle(request):
        path = unquote(request.url.path)
        requests.append((request.method, path))
        data = json.loads(request.content) if request.content else {}
        if path == "/repos/example/repo":
            return httpx.Response(200, json={"default_branch": "release"})
        if "/commits/" in path:
            ref = path.split("/commits/", 1)[1]
            return httpx.Response(
                200,
                json={
                    "sha": git(repo, "rev-parse", ref),
                    "commit": {
                        "tree": {"sha": git(repo, "rev-parse", ref + "^{tree}")}
                    },
                },
            )
        if path.endswith("/comments"):
            return httpx.Response(
                200, json=[] if request.method == "GET" else {"id": 1}
            )
        if path.endswith("/labels") and request.method == "POST":
            state["labels"].extend({"name": label} for label in data["labels"])
            return httpx.Response(200, json=state["labels"])
        if "/labels/" in path:
            return httpx.Response(200, json={"name": path.rsplit("/", 1)[-1]})
        if path.endswith("/issues/1"):
            if request.method == "PATCH":
                state.update(data)
            return httpx.Response(200, json=state)
        raise AssertionError(f"Unexpected provider request: {request.method} {path}")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )

    async def token(*_, **__):
        return "test-credential", None

    monkeypatch.setattr(GitHubService, "resolve_github_token", staticmethod(token))
    return state, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("target_input", [None, "", "refs/heads/release"])
@pytest.mark.parametrize(
    "initial_verdict", ["FULLY_IMPLEMENTED", "PARTIALLY_IMPLEMENTED"]
)
async def test_expanded_preset_requires_pr_for_candidate_and_closes_verified_target(
    candidate, github_boundary, tmp_path, monkeypatch, target_input, initial_verdict
):
    repo, current, report = candidate
    state, requests = github_boundary
    inputs = {
        "github_issue": {"repository": "example/repo", "number": 1},
        "constraints": "Preserve the callable interface.",
    }
    if target_input is not None:
        inputs["completion_target_ref"] = target_input
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(
                seed_dir=ROOT / "api_service/data/presets"
            )
            expanded = await catalog.expand_template(
                slug="github-issue-implement",
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context={},
            )
    steps = expanded["steps"]
    assert steps[1]["skill"]["id"] == "moonspec-assess"
    assert steps[5]["skill"]["args"]["completion_target_ref"] == (target_input or "")
    spec = RemediationLoopSpec.model_validate(
        steps[6]["annotations"]["remediationLoop"]
    )
    assert spec.verification_tool.inputs["completion_target_ref"] == (
        target_input or ""
    )
    assert spec.verification_tool.inputs["constraints"] == inputs["constraints"]
    assert steps[5]["skill"]["args"]["constraints"] == inputs["constraints"]
    parent = run_module.MoonMindRunWorkflow()
    parent._repo = "example/repo"
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(run_module.workflow, "now", lambda: datetime.now(timezone.utc))
    parent._record_assessment_context(
        {"assessmentVerdict": initial_verdict, "assessmentArtifactRef": "initial"}
    )
    assert parent._issue_implement_pr_required()
    initial = parent._moonspec_verify_gate_result({"verdict": "FULLY_IMPLEMENTED"})
    assert initial.verdict == "NO_DETERMINATION"
    gate = parent._moonspec_verify_gate_result(report)
    decision = decide_remediation_continuation(
        spec=spec,
        state=RemediationLoopState(
            loopId=spec.loop_id,
            phase="initial_verification_evaluating",
            consumedBudgets={},
        ),
        verdict=gate.verdict,
        gate_result_ref="artifact://objective",
    )
    assert decision.next_phase == "accepted"
    parent._publish_context["moonSpecGate"] = gate.to_payload()
    assert parent._issue_implement_pr_required(), (
        "Accepted candidate still needs its PR"
    )
    # Later output cannot rewrite the initial observation or its artifact.
    parent._record_assessment_context(
        {"assessmentVerdict": "FULLY_IMPLEMENTED", "assessmentArtifactRef": "later"}
    )
    assert parent._assessment_context["assessmentArtifactRef"] == "initial"
    assert parent._assessment_context["assessmentVerdict"] == initial_verdict
    store = ArtifactStore({"objective": report})
    final_inputs = dict(
        steps[-1]["tool"]["inputs"],
        previousOutputs={"moonSpecVerifyArtifactRef": "objective"},
    )
    result = await update_github_issue_status(
        final_inputs, {"temporal_artifact_service": store}
    )
    assert result.status == "FAILED"
    assert state["state"] == "open"
    assert not any(method != "GET" for method, _ in requests)
    assert store.reads == ["objective"]
    # Land identical content as a squash-shaped commit on the real target ref.
    squash = git(
        repo,
        "commit-tree",
        git(repo, "rev-parse", "HEAD^{tree}"),
        "-p",
        "release",
        "-m",
        "squash",
    )
    git(repo, "update-ref", "refs/heads/release", squash)
    current.update(portable.capture(repo, "example/repo", "release", target_mode=True))
    assert portable.reuse(report, current)["completionEligible"]
    # Rebinding target observation keeps the original tested subject/evidence.
    report["validatedRefs"]["acceptance"]["completionTarget"] = current[
        "completionTarget"
    ]
    parent._publish_context["moonSpecGate"] = report
    assert not parent._issue_implement_pr_required()
    result = await update_github_issue_status(
        final_inputs, {"temporal_artifact_service": store}
    )
    assert result.status == "COMPLETED", result.outputs
    assert state["state"] == "closed"
    assert result.outputs["sideEffect"]["operation"] == "github.issue.close"
    assert requests.count(("PATCH", "/repos/example/repo/issues/1")) == 1


@pytest.mark.asyncio
async def test_source_mismatch_expiry_and_unreadable_durable_proof_withhold_completion(
    candidate, github_boundary
):
    _, _, report = candidate
    state, requests = github_boundary
    for defect in ("source", "expiry", "missing-ref"):
        candidate_report = copy.deepcopy(report)
        if defect == "source":
            candidate_report["validatedRefs"]["acceptance"]["scope"]["sourceRef"] = (
                "example/repo#2"
            )
        if defect == "expiry":
            candidate_report["validatedRefs"]["acceptance"]["freshness"][
                "validUntil"
            ] = "2000-01-01T00:00:00Z"
        store = ArtifactStore(
            {} if defect == "missing-ref" else {"proof": candidate_report}
        )
        result = await update_github_issue_status(
            {
                "repository": "example/repo",
                "issueNumber": 1,
                "mode": "finalize_after_pr_or_done",
                "previousOutputs": {
                    "moonSpecVerifyArtifactRef": "proof",
                    "moonSpecVerify": report,
                    "assessmentVerdict": "FULLY_IMPLEMENTED",
                },
            },
            {"temporal_artifact_service": store},
        )
        assert result.status == "FAILED"
        assert result.outputs["remainingEvidence"]
    assert state["state"] == "open"
    assert not requests


@pytest.mark.asyncio
async def test_jira_finalizer_uses_same_live_target_contract(
    candidate, github_boundary
):
    repo, current, report = candidate
    report["validatedRefs"]["acceptance"]["scope"]["sourceRef"] = "EX-1"
    calls = []

    class Jira:
        async def get_issue(self, request):
            return {
                "key": "EX-1",
                "fields": {"status": {"name": "Done" if calls else "In Progress"}},
            }

        async def get_transitions(self, request):
            return {
                "transitions": [
                    {"id": "finish", "name": "Finish", "to": {"name": "Done"}}
                ]
            }

        async def transition_issue(self, request):
            calls.append(request.transition_id)
            return {}

    inputs = {
        "issueKey": "EX-1",
        "repository": "example/repo",
        "mode": "finalize_after_pr_or_done",
        "verificationPayload": report,
    }
    blocked = await update_jira_issue_status(inputs, jira_service_factory=Jira)
    assert blocked.status == "FAILED" and not calls
    git(repo, "update-ref", "refs/heads/release", git(repo, "rev-parse", "HEAD"))
    current.update(portable.capture(repo, "example/repo", "release", target_mode=True))
    report["validatedRefs"]["acceptance"]["completionTarget"] = current[
        "completionTarget"
    ]
    completed = await update_jira_issue_status(inputs, jira_service_factory=Jira)
    assert completed.status == "COMPLETED", completed.outputs
    assert calls == ["finish"]


@pytest.mark.asyncio
async def test_unavailable_mandatory_check_preserves_independent_checks_without_completion(
    candidate, github_boundary, tmp_path, monkeypatch
):
    _, _, report = candidate
    state, requests = github_boundary
    # Independent implementation checks actually ran in the fixture. A required
    # additional command is unavailable; neither that nor absent deployment
    # approval waives it or causes a production mutation.
    assert (tmp_path / "AC-1.json").is_file()
    with pytest.raises(FileNotFoundError):
        subprocess.run([str(tmp_path / "unavailable-required-check")], check=True)
    report["validatedRefs"]["acceptance"]["scope"]["requirementIds"].append("AC-3")
    parent = run_module.MoonMindRunWorkflow()
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(run_module.workflow, "now", lambda: datetime.now(timezone.utc))
    parent._assessment_context = {"assessmentVerdict": "PARTIALLY_IMPLEMENTED"}
    gate = parent._moonspec_verify_gate_result(report)
    assert gate.verdict == "NO_DETERMINATION"
    assert "mandatory requirement evidence" in gate.downgrade_reason
    blocked = await update_github_issue_status(
        {
            "repository": "example/repo",
            "issueNumber": 1,
            "mode": "finalize_after_pr_or_done",
            "verificationPayload": report,
        }
    )
    assert blocked.status == "FAILED" and blocked.outputs["remainingEvidence"]
    assert state["state"] == "open" and not requests
