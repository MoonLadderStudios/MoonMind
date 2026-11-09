"""Controlled finding → existing remediation → saved candidate → retest journey.

Real artifact/admission/publication owners, local Git and executable seeded app;
only Temporal dispatch and GitHub transport use the existing recording fixtures.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from temporalio.exceptions import ApplicationError, is_cancelled_exception

from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.remediation_context import (
    read_security_remediation_findings,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.integration.security.test_recurring_assessments import invoke, put, read
from tests.support.saved_work_capture import capture_saved_work, git
from tests.unit.publish.test_saved_work_publication_journey import (
    OPERATOR,
    _enforced_access,  # noqa: F401 - retain the publication fixture's enforced-access policy
    _persisted_result,
    _WorkerLost,
    journey,
)
from tests.unit.security.test_security_review import review_payload
from tests.unit.workflows.temporal.test_temporal_service import (
    _valid_user_workflow_parameters,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
ROOT = Path(__file__).resolve().parents[3]
SCOPE = {"assets": ["app/routes.py"], "exclusions": [], "repositoryExceptions": []}
PROBE = "import runpy; f=runpy.run_path('app/routes.py')['get_invoice']; rows={2:{'owner':'bob','amount':42}}; assert f(rows,2,'bob')==(200,42); assert f(rows,2,'alice')==(403,None)"


def probe(root):
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "exitCode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": PROBE,
    }


def observation(evidence_ref, *, failed, now):
    return {
        "schemaVersion": "security-observation/v1",
        "scope": SCOPE,
        "observedAt": now.isoformat(),
        "expiresAt": (now + timedelta(days=1)).isoformat(),
        "coverage": [
            {
                "source": "controlled-application",
                "tool": "invoice-owner-probe",
                "assets": SCOPE["assets"],
                "ruleIds": ["invoice-owner"],
                "status": "complete",
                "configRef": "probe:owner-positive-and-negative-v1",
                "intelligence": {"probeVersion": "v1"},
            }
        ],
        "provenance": {"probe": "invoice-owner-v1"},
        "findings": [
            {
                "source": "controlled-application",
                "tool": "invoice-owner-probe",
                "ruleId": "invoice-owner",
                "asset": "app/routes.py",
                "nativeId": "invoice-owner",
                "evidenceRefs": [evidence_ref],
            }
        ]
        if failed
        else [],
    }


async def test_seeded_finding_preserves_candidate_through_uncertainty_cancel_restart_and_retest(
    tmp_path, monkeypatch
):
    original = (ROOT / "tests/fixtures/security_review/vulnerable.py").read_text()
    fixed = (ROOT / "tests/fixtures/security_review/fixed.py").read_text()
    now = datetime.now(UTC)
    retained = {}

    async def saved_factory(artifacts):
        from api_service.db.models import (
            TemporalExecutionCanonicalRecord,
            TemporalExecutionRemediationLink,
        )
        from moonmind.workflows.temporal import service as service_module

        # Use the same real local artifact store as the existing publication fixture.
        monkeypatch.setattr(
            service_module,
            "TemporalArtifactService",
            lambda repository, **kw: TemporalArtifactService(
                repository, store=artifacts._store, **kw
            ),
        )
        adapter = MagicMock()
        adapter.start_workflow = AsyncMock(
            side_effect=[
                SimpleNamespace(run_id="assessment-run"),
                SimpleNamespace(run_id="repair-run"),
            ]
        )
        adapter.namespace_retention_seconds = AsyncMock(return_value=86400)
        executions = TemporalExecutionService(
            artifacts._repository._session, client_adapter=adapter
        )
        base = {
            "workflow_type": "MoonMind.UserWorkflow",
            "owner_id": UUID(OPERATOR),
            "input_artifact_ref": None,
            "plan_artifact_ref": None,
            "manifest_artifact_ref": None,
            "failure_policy": None,
        }
        source = await executions.create_execution(
            **base,
            title="Controlled ownership assessment",
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="assessment",
        )
        probe_root = tmp_path / "probe-original"
        (probe_root / "app").mkdir(parents=True)
        (probe_root / "app/routes.py").write_text(original)
        before = probe(probe_root)
        assert before["exitCode"] != 0
        before_ref = await put(
            artifacts, before, workflow_id=source.workflow_id, run_id=source.run_id
        )
        code_ref = await put(
            artifacts,
            {"path": "app/routes.py", "source": original},
            workflow_id=source.workflow_id,
            run_id=source.run_id,
        )
        native = observation(before_ref, failed=True, now=now)
        review = review_payload()
        review["candidateRef"] = (
            "sha256:" + hashlib.sha256(original.encode()).hexdigest()
        )
        review["interpretations"][0]["evidenceRefs"] = [code_ref, before_ref]
        review_ref = await put(
            artifacts, review, workflow_id=source.workflow_id, run_id=source.run_id
        )
        native_ref = await put(
            artifacts, native, workflow_id=source.workflow_id, run_id=source.run_id
        )
        request = {
            "scope": SCOPE,
            "tools": ["retained-report"],
            "reportRefs": [native_ref],
            "reviewRef": review_ref,
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {"baseSeconds": 30, "maxSeconds": 60, "progressStallSeconds": 30},
        }
        first = await invoke(
            artifacts, request, workflow_id=source.workflow_id, run_id=source.run_id
        )
        selected = await read_security_remediation_findings(
            service=artifacts,
            workflow_id=source.workflow_id,
            run_id=source.run_id,
            review_artifact_ref=review_ref,
            access=ArtifactAccessContext.for_execution(
                source.workflow_id, principal="service:test"
            ),
        )
        assert selected["findings"][0]["candidateRef"] == review["candidateRef"]
        params = {
            "workflow": {
                "instructions": "Investigate pinned ownership finding; separately admitted code fix only",
                "remediation": {
                    "target": {
                        "workflowId": source.workflow_id,
                        "runId": source.run_id,
                        "securityFinding": {
                            "reviewArtifactRef": review_ref,
                            "interpretationId": "invoice-owner",
                        },
                    },
                    "mode": "snapshot",
                    "authorityMode": "observe_only",
                    "trigger": {"type": "manual"},
                },
            }
        }
        repair = await executions.create_execution(
            **base,
            title="Scoped ownership fix",
            initial_parameters=params,
            idempotency_key="repair",
        )
        stored = await artifacts._repository._session.get(
            TemporalExecutionCanonicalRecord, repair.workflow_id
        )
        assert "assessmentScope" not in stored.parameters
        link = await artifacts._repository._session.get(
            TemporalExecutionRemediationLink, repair.workflow_id
        )
        context_ref = link.context_artifact_ref
        replay = await executions.create_execution(
            **base,
            title="Scoped ownership fix",
            initial_parameters=params,
            idempotency_key="repair",
        )
        assert (
            replay.workflow_id == repair.workflow_id
            and adapter.start_workflow.await_count == 2
        )
        retained.update(
            source=source,
            repair=repair,
            request=request,
            before=before_ref,
            review=review_ref,
            first=first,
            context=context_ref,
            native=native,
            artifacts=artifacts,
        )
        return await capture_saved_work(
            tmp_path,
            {"app/routes.py": original},
            lambda repo: (repo / "app/routes.py").write_text(fixed),
            artifact_service=artifacts,
            workflow_id=repair.workflow_id,
            run_id=repair.run_id,
        )

    with monkeypatch.context() as publication_patch:
        async with journey(
            tmp_path,
            publication_patch,
            destination_files={"README.md": "Controlled destination\n"},
            saved_work_factory=saved_factory,
            admit_publication=False,
        ) as state:
            assert (
                not state.saved.source_repo.exists()
            )  # Restart cannot depend on lost workspace.
            original_saved = state.saved_bytes()
            contract = state.contract(
                objective="pr", baseBranch="main", strategy="additive_import"
            )
            with pytest.raises(ApplicationError):
                await state.run({**contract, "admittedPrincipal": "workflow:unrelated"})
            assert not state.pushes() and not state.provider.creates
            await state.admit(contract, "mm:source:saved-work-publication:x")

            async def lose_push_ack(attempt):
                if attempt == 1:
                    raise _WorkerLost()

            async def cancel_pr(attempt):
                raise asyncio.CancelledError()

            state.after["publication_recovery.saved_work_push"] = lose_push_ack
            state.hooks["publication_recovery.saved_work_pull_request"] = cancel_pr
            with pytest.raises(BaseException) as cancelled:
                await state.run(contract)
            assert is_cancelled_exception(cancelled.value)
            cancelled_result = await _persisted_result(state)
            assert cancelled_result["push"]["status"] in {"pushed", "reconciled"}
            assert cancelled_result["pullRequest"]["status"] == "unconfirmed"
            assert not state.provider.creates and len(state.pushes()) == 1
            selected_head = git(state.remote, "rev-parse", "refs/heads/saved/work")
            state.hooks.clear()  # Explicit resumption, never an automatic successor to cancellation.
            state.provider.unavailable_reads = 5
            with pytest.raises(ApplicationError):
                await state.run(contract)
            failed_publication = await _persisted_result(state)
            assert failed_publication["pullRequest"]["status"] == "unconfirmed"
            result = await state.run(contract)
            assert result["outcome"] == "published"
            assert (
                result["candidate"]
                == failed_publication["candidate"]
                == cancelled_result["candidate"]
            )
            assert result["candidate"]["headSha"] == selected_head
            assert len(state.pushes()) == 1 and len(state.provider.creates) == 1
            assert state.saved_objects_unchanged(original_saved)
            assert result["implementationRerun"] is False
            assert await state.use_claims() == []
            # Retest exactly the published candidate, not a rebuilt source tree.
            retest_root = tmp_path / "retest-published"
            (retest_root / "app").mkdir(parents=True)
            (retest_root / "app/routes.py").write_text(
                git(state.remote, "show", f"{selected_head}:app/routes.py")
            )
            after = probe(retest_root)
            assert after["exitCode"] == 0
            # Exit the publication SDK context before invoking the assessment owner.
            retained.update(after=after, publication=result, candidate=selected_head)
            publication_patch.undo()
            artifacts = state.service
            source, repair = retained["source"], retained["repair"]
            source_access = ArtifactAccessContext.for_execution(
                source.workflow_id, principal="service:test"
            )
            await artifacts.delegate_read_access(
                artifact_ids=[retained["first"]["comparisonRef"]],
                workflow_id=repair.workflow_id,
                access=source_access,
            )
            original_comparison = await read(
                artifacts,
                retained["first"]["comparisonRef"],
                principal=f"workflow:{repair.workflow_id}",
            )
            finding_id = original_comparison["findings"][0]["findingId"]
            publication_ref = result["resultArtifactRef"]["artifact_id"]
            await artifacts.delegate_read_access(
                artifact_ids=[publication_ref],
                workflow_id=repair.workflow_id,
                access=ArtifactAccessContext.for_execution(
                    result["destinationWorkflowId"], principal="service:test"
                ),
            )
            after_ref = await put(
                artifacts,
                {
                    **after,
                    "candidateHead": selected_head,
                    "publicationRef": publication_ref,
                    "originalEvidenceRef": retained["before"],
                },
                workflow_id=repair.workflow_id,
                run_id=repair.run_id,
            )
            passing = observation(after_ref, failed=False, now=now)
            after_observation_ref = await put(
                artifacts, passing, workflow_id=repair.workflow_id, run_id=repair.run_id
            )
            prior_review = await read(
                artifacts,
                retained["review"],
                principal=f"workflow:{repair.workflow_id}",
            )
            prior_review["candidateRef"] = "git:" + selected_head
            prior_review["retests"] = [
                {
                    "hypothesisId": "invoice-owner",
                    "originalEvidenceRefs": prior_review["interpretations"][0][
                        "evidenceRefs"
                    ],
                    "currentEvidenceRefs": [after_ref],
                    "changedAssets": SCOPE["assets"],
                    "coveredAssets": SCOPE["assets"],
                    "coverage": "complete",
                    "limitations": [
                        "Local application and recording GitHub transport only"
                    ],
                }
            ]
            after_review_ref = await put(
                artifacts,
                prior_review,
                workflow_id=repair.workflow_id,
                run_id=repair.run_id,
            )
            retest_request = {
                **retained["request"],
                "reportRefs": [after_observation_ref],
                "reviewRef": after_review_ref,
                "baselineRef": retained["first"]["comparisonRef"],
            }

            async def assess(request, key):
                output = await invoke(
                    artifacts,
                    request,
                    workflow_id=repair.workflow_id,
                    run_id=repair.run_id,
                    key=key,
                )
                return await read(
                    artifacts,
                    output["comparisonRef"],
                    principal=f"workflow:{repair.workflow_id}",
                ), output

            # Neither published candidate, green regression nor full review coverage
            # is itself a reviewed security disposition.
            unreviewed, _ = await assess(retest_request, "unreviewed")
            assert unreviewed["findings"][0]["state"] == "absent_comparable"
            decision = {
                "findingId": finding_id,
                "disposition": "fix_retest",
                "reason": "Same positive/negative ownership probe passes on the published candidate",
                "evidenceRefs": [retained["before"], after_ref, publication_ref],
                "author": "controlled test",
                "source": "controlled-application",
                "scope": SCOPE,
                "reviewAt": (now + timedelta(hours=1)).isoformat(),
                "reviewConditions": ["candidate, probe or environment changes"],
                "retestOutcome": "passed",
            }

            async def triaged(request, verdict, key):
                ref = await put(
                    artifacts,
                    [verdict],
                    workflow_id=repair.workflow_id,
                    run_id=repair.run_id,
                )
                return await assess({**request, "decisionsRef": ref}, key)

            verified, verified_output = await triaged(
                retest_request, decision, "verified"
            )
            assert verified["findings"][0]["state"] == "remediated_verified"
            for outcome in ("failed", "inconclusive"):
                unresolved, _ = await triaged(
                    retest_request, {**decision, "retestOutcome": outcome}, outcome
                )
                assert unresolved["findings"][0]["state"] != "remediated_verified"
            for change in ("coverage", "source"):
                incomplete = copy.deepcopy(passing)
                incomplete["coverage"][0][
                    "status" if change == "coverage" else "source"
                ] = "partial" if change == "coverage" else "different-environment"
                ref = await put(
                    artifacts,
                    incomplete,
                    workflow_id=repair.workflow_id,
                    run_id=repair.run_id,
                )
                unverified, _ = await triaged(
                    {**retest_request, "reportRefs": [ref]}, decision, change
                )
                assert unverified["findings"][0]["state"] == "not_reassessed"
            changed_intelligence = copy.deepcopy(passing)
            changed_intelligence["coverage"][0]["intelligence"]["probeVersion"] = "v2"
            changed_ref = await put(
                artifacts,
                changed_intelligence,
                workflow_id=repair.workflow_id,
                run_id=repair.run_id,
            )
            changed, _ = await assess(
                {**retest_request, "reportRefs": [changed_ref]}, "changed-intelligence"
            )
            assert changed["findings"][0]["changedIntelligence"]
            assert changed["findings"][0]["state"] == "absent_comparable"
            for closure in ("fixed", "dismissed", "resolved"):
                for coverage in ("complete", "partial", "missing", "source"):
                    upstream = copy.deepcopy(passing)
                    upstream["upstreamClosed"] = {finding_id: closure}
                    if coverage == "partial":
                        upstream["coverage"][0]["status"] = "partial"
                    elif coverage == "missing":
                        upstream["coverage"][0]["ruleIds"] = ["different-rule"]
                    elif coverage == "source":
                        upstream["coverage"][0]["source"] = "different-environment"
                    upstream_ref = await put(
                        artifacts,
                        upstream,
                        workflow_id=repair.workflow_id,
                        run_id=repair.run_id,
                    )
                    request = {**retest_request, "reportRefs": [upstream_ref]}
                    for outcome in (None, "failed", "inconclusive", "passed"):
                        key = f"closure-{closure}-{coverage}-{outcome}"
                        closed, _ = (
                            await assess(request, key)
                            if outcome is None
                            else await triaged(
                                request, {**decision, "retestOutcome": outcome}, key
                            )
                        )
                        sufficient = coverage == "complete" and outcome == "passed"
                        row = closed["findings"][0]
                        assert row["state"] == (
                            "remediated_verified" if sufficient else "closed_upstream"
                        )
                        assert row["verification"] == (
                            "retest_passed" if sufficient else None
                        )
                        assert closed["observation"]["upstreamClosed"] == {
                            finding_id: closure
                        }
            original_ref = await put(
                artifacts,
                retained["native"],
                workflow_id=repair.workflow_id,
                run_id=repair.run_id,
            )
            contradicted, _ = await assess(
                {
                    **retest_request,
                    "reportRefs": [original_ref],
                    "baselineRef": verified_output["comparisonRef"],
                },
                "contradicted",
            )
            assert contradicted["findings"][0]["state"] == "still_observed"
            assert (
                "retest_contradicted" in contradicted["decisions"][0]["reviewReasons"]
            )
            # The investigation's immutable source snapshot still names the old
            # hypothesis/candidate after later reports and publication retries.
            context = await read(
                artifacts,
                retained["context"],
                principal=f"workflow:{repair.workflow_id}",
            )
            assert (
                context["securityEvidence"]["candidateRef"]
                == "sha256:" + hashlib.sha256(original.encode()).hexdigest()
            )
            assert (
                context["securityEvidence"]["reviewArtifactRef"] == retained["review"]
            )
