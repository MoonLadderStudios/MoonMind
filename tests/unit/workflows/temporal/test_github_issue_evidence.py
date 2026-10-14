"""Actual GitHub bytes persist through the production artifact-store boundary."""

import hashlib
import json

import pytest

from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.unit.workflows.adapters.test_github_actions_evidence import (
    HEAD,
    REPO,
    failed_job_fixture,
    fixture,
)
from tests.unit.workflows.temporal.test_artifacts import temporal_db


@pytest.mark.asyncio
async def test_failed_job_bytes_persist_without_provider_artifact_ids(
    monkeypatch, tmp_path
):
    from moonmind.workflows.temporal.activities.github_issue_evidence import (
        collect_github_issue_evidence,
    )

    github, _state, _requests = failed_job_fixture(monkeypatch)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        result = await collect_github_issue_evidence(
            {
                "repository": REPO,
                "prNumber": 7,
                "headSha": HEAD,
                "workflowId": "work-1",
                "runId": "run-1",
            },
            artifact_service=service,
            service=github,
        )
        assert result["status"] == "ready" and len(result["evidenceRefs"]) == 2
        _, raw_manifest = await service.read(
            artifact_id=result["artifactRef"].removeprefix("artifact://"),
            principal="workflow:work-1",
        )
        files = json.loads(raw_manifest)["files"]
        for item in files:
            assert (
                item["jobId"] == 301
                and item["runAttempt"] == 1
                and item["runId"] == 101
            )
            assert "artifactId" not in item
            _, body = await service.read(
                artifact_id=item["artifactRef"].removeprefix("artifact://"),
                principal="workflow:work-1",
            )
            assert hashlib.sha256(body).hexdigest() == item["sha256"]
            if item["sourceType"] == "job-log":
                assert b"error: unknown type Widget" in body
            else:
                assert json.loads(body)["job"]["conclusion"] == "failure"


@pytest.mark.asyncio
async def test_collection_persists_materializable_bytes_and_compact_identity(
    monkeypatch, tmp_path
):
    from moonmind.workflows.temporal.activities.github_issue_evidence import (
        collect_github_issue_evidence,
    )

    github, _state, _requests = fixture(monkeypatch)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        payload = {
            "repository": REPO,
            "prNumber": 7,
            "headSha": HEAD,
            "workflowId": "work-1",
            "runId": "run-1",
            "namespace": "moonmind",
        }
        result = await collect_github_issue_evidence(
            payload, artifact_service=service, service=github
        )
        assert result["status"] == "ready"
        assert result["subject"] == {
            "repository": REPO,
            "revision": HEAD,
            "contentDigest": "git-tree:" + "c" * 40,
        }
        assert "payload" not in json.dumps(result)
        assert len(result["evidenceRefs"]) == 1
        ref = result["evidenceRefs"][0]
        _, stored = await service.read(
            artifact_id=ref.removeprefix("artifact://"), principal="workflow:work-1"
        )
        assert stored == b'{"tests":12}'
        assert (
            result["evidenceSet"][ref] == "sha256:" + hashlib.sha256(stored).hexdigest()
        )
        _, manifest_bytes = await service.read(
            artifact_id=result["artifactRef"].removeprefix("artifact://"),
            principal="workflow:work-1",
        )
        manifest = json.loads(manifest_bytes)
        assert manifest["files"][0]["artifactRef"] == ref
        assert manifest["files"][0]["runId"] == 101
        assert manifest["files"][0]["runAttempt"] == 1
        assert manifest["evidenceSetDigest"] == result["evidenceSetDigest"]
        repeated = await collect_github_issue_evidence(
            payload, artifact_service=service, service=github
        )
        assert repeated["semanticDigest"] == result["semanticDigest"]


@pytest.mark.asyncio
async def test_verify_candidate_reads_head_and_tree_without_artifact_download(
    monkeypatch,
):
    from moonmind.workflows.temporal.activities.github_issue_evidence import (
        collect_github_issue_evidence,
    )

    github, _state, requests = fixture(monkeypatch)
    result = await collect_github_issue_evidence(
        {
            "action": "verify_candidate",
            "repository": REPO,
            "prNumber": 7,
            "headSha": HEAD,
            "workflowId": "work-1",
        },
        service=github,
    )
    assert result["status"] == "ready"
    assert result["pullRequestOpen"] is True and result["draft"] is True
    assert not any("/actions/" in request.url.path for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("unrelated", [None, "expired", "pending"])
@pytest.mark.parametrize("initial_verdict", ["BLOCKED", "NO_DETERMINATION"])
async def test_published_pr_real_bytes_verifier_and_durable_owner_complete_same_candidate(
    monkeypatch, tmp_path, unrelated, initial_verdict
):
    """Production publisher/reader/store/owner; only the external agent is controlled."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.workflows.adapters import github_service as github_module
    from moonmind.workflows.temporal import activity_runtime
    from moonmind.workflows.temporal.agent_result_payloads import (
        build_acceptance_projection,
        compact_moonspec_verify_metadata,
    )
    from moonmind.workflows.temporal.remediation_loop import RemediationLoopPhase
    from moonmind.workflows.temporal.workflows import run as run_module
    from tests.unit.workflows.skills.test_acceptance_source_binding import _digest
    from tests.unit.workflows.temporal.test_remediation_loop import _spec, _state

    github, state, requests = fixture(monkeypatch)
    if unrelated:
        from copy import deepcopy

        if unrelated == "expired":
            other = deepcopy(state["artifact"])
            other.update(id=202, name="optional-diagnostics", expired=True)
            state["artifacts"] = [state["artifact"], other]
        else:
            other = deepcopy(state["run"])
            other.update(id=102, workflow_id=72, status="in_progress", conclusion=None)
            state["runs"] = [state["run"], other]
    state["enable_publication"] = True
    github.resolve_github_token = AsyncMock(return_value=("admitted-token", None))
    monkeypatch.setattr(github_module, "GitHubService", lambda: github)
    monkeypatch.setattr(
        activity_runtime, "_admitted_activity_workflow_id", lambda: "issue-owner"
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(
        run_module.workflow, "now", lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="issue-owner",
            run_id="run-1",
            namespace="moonmind",
            task_queue="workflow",
            start_time=datetime(2026, 10, 14, tzinfo=timezone.utc),
        ),
    )
    owner = run_module.MoonMindRunWorkflow()
    owner._repo = REPO
    owner._principal = lambda: "workflow:issue-owner"
    owner._assessment_context = {
        "assessmentArtifactRef": "artifact://historic-assessment",
        "briefArtifactRef": "artifact://source-brief",
    }
    owner._moonspec_gate_verdict = initial_verdict
    owner._publish_context = {
        "headSha": HEAD,
        "branch": "issue-2740",
        "baseRef": "main",
        "moonSpecGate": {
            "verdict": initial_verdict,
            "recommendedNextAction": "blocked",
        },
    }
    owner._remediation_loop_spec, owner._remediation_loop_state = _spec(), _state()
    owner._update_memo = lambda: None
    owner._update_search_attributes = lambda: None
    owner._wait_if_paused_at_safe_boundary = AsyncMock()
    owner._resolve_agent_node_skillset_ref = AsyncMock(
        return_value="artifact://resolved-verifier-skillset"
    )
    owner._workflow_child_task_queue = lambda: "workflow"
    nodes = [
        {
            "id": "verify",
            "annotations": {"issueImplementRole": "moonspec-verification-gate"},
            "tool": {"type": "agent_runtime", "name": "codex_cli"},
            "inputs": {
                "selectedSkill": "moonspec-verify",
                "instructions": "Verify original criterion",
                "runtime": {"mode": "codex_cli"},
            },
        }
    ]

    async with temporal_db(tmp_path) as sessions, sessions() as session:
        store = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        activities = activity_runtime.TemporalIntegrationActivities(
            artifact_service=store
        )
        saved = []

        async def persist(*, name, payload):
            body = json.dumps(payload).encode()
            artifact, _ = await store.create(
                principal="workflow:issue-owner",
                content_type="application/json",
                metadata_json={"name": name},
            )
            await store.write_complete(
                artifact_id=artifact.artifact_id,
                principal="workflow:issue-owner",
                payload=body,
            )
            saved.append((name, payload, artifact.artifact_id))
            return f"artifact://{artifact.artifact_id}"

        owner._write_json_artifact = persist
        report_reads = []

        async def read_report(name, request, **_kwargs):
            assert (
                name == "artifact.read" and request.principal == "workflow:issue-owner"
            )
            report_reads.append(request.artifact_ref)
            _, body = await store.read(
                artifact_id=str(request.artifact_ref).removeprefix("artifact://"),
                principal=request.principal,
            )
            return body

        monkeypatch.setattr(run_module, "execute_typed_activity", read_report)

        async def publish_projected_gate(report, name):
            report["acceptanceScopeContract"] = "source-bound/v1"
            ref = await persist(name=name, payload=report)
            raw = json.dumps(report).encode()
            descriptor = build_acceptance_projection(
                report,
                report_ref=ref,
                report_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
            )
            projected = compact_moonspec_verify_metadata(
                {**report, "gateResultRef": ref}, acceptance_projection=descriptor
            )
            assert "acceptance" not in projected["validatedRefs"]
            return projected

        if initial_verdict == "NO_DETERMINATION":
            scope = {
                "sourceRef": f"{REPO}#2740",
                "sourceDigest": "sha256:original",
                "complete": True,
                "requirementIds": ["AC-1"],
            }
            initial_binding = {
                "schemaVersion": "acceptance/v1",
                "subject": {
                    "repository": REPO,
                    "revision": HEAD,
                    "contentDigest": "git-tree:" + "c" * 40,
                },
                "scope": scope,
                "completionTarget": {
                    "ref": "refs/heads/main",
                    "revision": "base",
                    "contentDigest": "git-tree:base",
                },
                "freshness": {"policy": "content"},
                "evidenceSet": {},
                "evidenceSetDigest": _digest({}),
                "evidence": [],
                "verificationMap": [
                    {
                        "id": "AC-1",
                        "sourceRef": scope["sourceRef"],
                        "sourceDigest": scope["sourceDigest"],
                        "sourceLocator": "acceptance[0]",
                        "classification": "mandatory",
                        "requiredBehavior": "Execute twelve boundary checks. " * 700,
                        "executableChecks": ["pytest boundary"],
                        "proofMethods": ["automated_tests"],
                        "owner": "existing CI",
                    }
                ],
            }
            initial_report = {
                "verdict": "NO_DETERMINATION",
                "recommendedNextAction": "blocked",
                "validatedRefs": {"acceptance": initial_binding},
                "remainingWork": [
                    {
                        "requirement": "AC-1",
                        "gapType": "verification",
                        "remainingWork": "Collect the exact-head completed CI report",
                    }
                ],
            }
            projected = await publish_projected_gate(
                initial_report, "reports/initial-inconclusive.json"
            )
            initial_projection = projected["validatedRefs"]["acceptanceProjection"]
            hydrated = await owner._hydrate_acceptance_result(
                {"status": "COMPLETED", "outputs": {"moonSpecVerify": projected}}
            )
            owner._record_moonspec_verify_gate(
                node_id="initial-verify", outputs=hydrated["outputs"]
            )
            initial_context = owner._publish_context["moonSpecGate"]
            assert initial_context["verdict"] == "NO_DETERMINATION"
            assert (
                initial_context["invalid"] is False
                and initial_context["degraded"] is False
            )
            assert initial_context["evidenceContinuationEligible"] is True
        policy = owner._moonspec_draft_publication_policy(
            environment_blocked_enabled=True, additional_work_enabled=False
        )
        assert policy == "draft_pr_on_environment_blocked"
        owner._activate_moonspec_draft_publication(
            "Actual proof was pending", policy=policy
        )
        assert owner._apply_blocking_moonspec_gate_to_publish() is False
        assert not state.get("published")
        published = await github.create_pull_request(
            repo=REPO,
            head="issue-2740",
            base="main",
            title="Fix issue 2740",
            body=owner._moonspec_draft_publication_body_section(),
            draft=True,
        )
        assert published.created and published.head_sha == HEAD and state["published"]
        owner._publish_context["pullRequestUrl"] = published.url

        async def dispatch(name, payload, **_kwargs):
            assert name == "github_issue.collect_evidence"
            return await activities.github_issue_collect_evidence(payload)

        verifier_calls = []

        async def external_verifier(name, request, **_kwargs):
            assert name == "MoonMind.AgentRun"
            verifier_calls.append(request)
            assert request.parameters["repositoryOperation"] == "read"
            assert request.parameters["publishMode"] == "none"
            assert (
                request.workspace_spec["repositoryTarget"]["revision"]["commitSha"]
                == HEAD
            )
            manifest_ref = request.parameters["evidenceManifestRef"]
            assert manifest_ref in request.input_refs
            _, manifest_bytes = await store.read(
                artifact_id=manifest_ref.removeprefix("artifact://"),
                principal="workflow:issue-owner",
            )
            manifest = json.loads(manifest_bytes)
            assert 101 in manifest["runIds"]
            if unrelated:
                assert manifest["collectionComplete"] is False
            for ref, digest in request.parameters["evidenceSet"].items():
                assert ref in request.input_refs
                _, body = await store.read(
                    artifact_id=ref.removeprefix("artifact://"),
                    principal="workflow:issue-owner",
                )
                assert "sha256:" + hashlib.sha256(body).hexdigest() == digest
                assert json.loads(body)["tests"] == 12
            scope = {
                "sourceRef": f"{REPO}#2740",
                "sourceDigest": "sha256:original",
                "complete": True,
                "requirementIds": ["AC-1"],
            }
            plan = {
                "id": "AC-1",
                "sourceRef": scope["sourceRef"],
                "sourceDigest": scope["sourceDigest"],
                "sourceLocator": "acceptance[0]",
                "classification": "mandatory",
                "requiredBehavior": "Execute twelve boundary checks. " * 700,
                "executableChecks": ["pytest boundary"],
                "proofMethods": ["automated_tests"],
                "owner": "existing CI",
            }
            binding = {
                "schemaVersion": "acceptance/v1",
                "subject": request.parameters["acceptanceSubject"],
                "scope": scope,
                "completionTarget": {
                    "ref": "refs/heads/main",
                    "revision": "base",
                    "contentDigest": "git-tree:base",
                },
                "freshness": {"policy": "content"},
                "verificationMap": [plan],
                "evidenceSet": request.parameters["evidenceSet"],
                "evidenceSetDigest": request.parameters["evidenceSetDigest"],
                "evidence": [
                    {
                        "requirementId": "AC-1",
                        "status": "VERIFIED",
                        "obligationDigest": _digest(plan),
                        "evidenceRefs": list(request.parameters["evidenceSet"]),
                        "proofMethod": "automated_tests",
                    }
                ],
            }
            gate = {
                "verdict": "FULLY_IMPLEMENTED",
                "recommendedNextAction": "advance",
                "validatedRefs": {"acceptance": binding},
            }
            assert len(json.dumps(gate).encode()) > 16384
            projected = await publish_projected_gate(gate, "reports/verified-gate.json")
            return {"metadata": {"moonSpecVerify": projected}}

        monkeypatch.setattr(run_module.workflow, "execute_activity", dispatch)
        monkeypatch.setattr(
            run_module.workflow, "execute_child_workflow", external_verifier
        )
        await owner._resume_published_issue_evidence(
            parameters={"publishMode": "pr"}, ordered_nodes=nodes
        )
        assert len(verifier_calls) == 1
        assert owner._remediation_loop_state.phase == RemediationLoopPhase.ACCEPTED
        assert owner._remediation_loop_state.consumed_budgets.attempts == 0
        assert owner._remediation_loop_state.consumed_budgets.evidence_retries == 1
        assert owner._moonspec_draft_publication_reason is None
        assert state["marked_ready"] is True
        assert len(report_reads) == (2 if initial_verdict == "NO_DETERMINATION" else 1)
        if initial_verdict == "NO_DETERMINATION":
            _, original_bytes = await store.read(
                artifact_id=initial_projection["reportRef"].removeprefix("artifact://"),
                principal="workflow:issue-owner",
            )
            assert (
                "sha256:" + hashlib.sha256(original_bytes).hexdigest()
                == initial_projection["reportDigest"]
            )
            assert json.loads(original_bytes)["verdict"] == "NO_DETERMINATION"
            assert (
                json.loads(original_bytes)["remainingWork"][0]["gapType"] == "verification"
            )
        assert owner._publish_context["pullRequestUrl"] == published.url
        decisions = [
            payload
            for name, payload, _ref in saved
            if name == "reports/issue_evidence_continuation.json"
        ]
        assert decisions[-1]["status"] == "accepted" and 101 in decisions[-1]["runIds"]
        assert (
            sum(
                request.method == "POST" and request.url.path.endswith("/pulls")
                for request in requests
            )
            == 1
        )
        assert not any(
            request.url.path.endswith(("/rerun", "/dispatches", "/merge"))
            for request in requests
        )
