"""The resolved portable gate steers a real correction within one durable turn owner."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from api_service.db.models import (
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.attempt_completion import complete_skill_turns
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.omnigent.test_generic_platform_production_services import _exact_plan
from tests.unit.test_pr_resolver_finish_mode import _mergeable_snapshot

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]
ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("deferred", [False, True])
async def test_portable_skill_repairs_ci_then_verifies_new_remote_head(
    tmp_path, deferred
):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(candidate), *args], text=True
        ).strip()

    git("init", "-q", "-b", "feature")
    git("config", "user.name", "Qualification")
    git("config", "user.email", "qualification@example.invalid")
    git("remote", "add", "origin", str(remote))
    (candidate / "answer.py").write_text("answer = 0\n")
    git("add", "answer.py")
    git("commit", "-qm", "broken candidate")
    git("push", "-q", "origin", "feature")
    original_head = git("rev-parse", "HEAD")
    active = tmp_path / "active-skills"
    shutil.copytree(ROOT / ".agents/skills/pr-resolver", active / "pr-resolver")
    shutil.copytree(ROOT / ".agents/skills/fix-ci", active / "fix-ci")
    shutil.copytree(ROOT / ".agents/skills/_shared", active / "_shared")
    # GitHub's read-only PR response is a controlled service fixture. Publication
    # head verification still crosses a real Git remote and the portable helper.
    cli = tmp_path / "bin"
    cli.mkdir()
    gh = cli / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nassert sys.argv[1:3] == ['pr','view']\nprint(json.dumps({'state':'OPEN','mergedAt':None,'mergeCommit':None}))\n"
    )
    gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(cli) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(ROOT),
        "MOONMIND_STEP_EXECUTION_ID": "step:resolver",
        "MOONMIND_ACTIVE_SKILLS_DIR": str(active),
    }
    result_path = candidate / "var/pr_resolver/result.json"
    snapshot_path = candidate / "var/pr_resolver/snapshot.json"
    result_path.parent.mkdir(parents=True)
    contract = {
        "contractId": "pr_resolver_terminal.v1",
        "relativePath": "var/pr_resolver/result.json",
        "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
        "executionRef": "step:resolver",
    }
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="resolver",
        idempotencyKey="resolver-attempt",
        terminalContract=contract,
    )
    calls = []
    observed = []

    def portable_gate():
        check = subprocess.run(
            [sys.executable, "-c", "from answer import answer; assert answer == 42"],
            cwd=candidate,
            capture_output=True,
        )
        snapshot = _mergeable_snapshot()
        snapshot["pr"]["headRefOid"] = git("rev-parse", "HEAD")
        snapshot["ci"]["hasFailures"] = check.returncode != 0
        snapshot["commentsSummary"]["hasDeferredComments"] = deferred
        snapshot_path.write_text(json.dumps(snapshot))
        completed = subprocess.run(
            [
                sys.executable,
                str(active / "pr-resolver/bin/pr_resolve_finalize.py"),
                "--pr",
                "350",
                "--skip-refresh",
                "--snapshot-path",
                str(snapshot_path),
                "--result-path",
                str(result_path),
                "--finish-mode",
                "fix_only",
                "--no-require-fresh-review",
                "--strict-exit-codes",
            ],
            cwd=candidate,
            env=env,
            capture_output=True,
            text=True,
        )
        assert completed.returncode in {0, 2}, completed.stderr
        result = json.loads(result_path.read_text())
        observed.append((snapshot["pr"]["headRefOid"], result["status"]))
        if result["status"] == "review_clean":
            publish = subprocess.run(
                [
                    sys.executable,
                    str(active / "_shared/publish_evidence.py"),
                    "from-pr-resolver-result",
                    "--result",
                    str(result_path),
                    "--snapshot",
                    str(snapshot_path),
                ],
                cwd=candidate,
                env=env,
                capture_output=True,
                text=True,
            )
            assert publish.returncode == 0, publish.stderr

    async with isolated_postgres(
        [OmnigentExecutionPlanRecord.__table__, OmnigentRuntimeBindingRecord.__table__]
    ) as sessions:
        plan = _exact_plan("opencode-go/model")
        await DbExecutionPlanStore(sessions).persist(plan)
        store = DbRuntimeBindingStore(sessions)
        binding = await store.create_initial(
            execution_plan_ref=plan.planRef,
            idempotency_key=request.idempotency_key,
            provider_leases={},
        )
        for state in (
            RuntimeBindingState.credentials_materialized,
            RuntimeBindingState.host_allocating,
            RuntimeBindingState.host_ready,
            RuntimeBindingState.session_creating,
        ):
            binding = await store.update(
                binding.bindingId,
                expected_revision=binding.revision,
                expected_fencing_generation=binding.fencingGeneration,
                state=state,
            )

        async def driver(request, *, session_authority_sink, **kwargs):
            calls.append(request.idempotency_key)
            await session_authority_sink.session_created("same-resolver-session")
            if len(calls) > 1:
                assert kwargs["resume_session_id"] == "same-resolver-session"
                assert "run_full_remediation" in kwargs["first_message_text"]
                assert (active / "fix-ci/SKILL.md").is_file()
                # This is the agent callback: make and verify an actual bounded
                # correction, commit it, and publish the replacement candidate.
                (candidate / "answer.py").write_text("answer = 42\n")
                git("add", "answer.py")
                git("commit", "-qm", "repair failing acceptance check")
                git("push", "-q", "origin", "feature")
            await asyncio.to_thread(portable_gate)
            return AgentRunResult(
                summary="portable Skill gate evaluated",
                metadata={"omnigentSessionId": "same-resolver-session"},
            )

        async def inspect(_request):
            return evaluate_terminal_evidence(contract, workspace_path=str(candidate))

        sink = RuntimeBindingSessionAuthoritySink(store, binding)
        await complete_skill_turns(
            request=request, sink=sink, driver=driver, inspect_terminal=inspect
        )
        verdict = await inspect(request)
        if deferred:
            assert not verdict.satisfied
            assert verdict.failure_code == "PR_RESOLVER_MANUAL_REVIEW"
            assert calls == ["resolver-attempt"]
            assert git("rev-parse", "HEAD") == original_head
        else:
            assert verdict.satisfied, verdict
            assert len(calls) == 2
            assert observed == [
                (original_head, "blocked"),
                (git("rev-parse", "HEAD"), "review_clean"),
            ]
            assert observed[0][0] != observed[1][0]
            publication = json.loads(
                (candidate / "artifacts/publish_result.json").read_text()
            )
            assert publication["remoteVerified"] and not publication["merged"]
            assert (
                publication["localHead"]
                == publication["remoteBranchHead"]
                == git("rev-parse", "HEAD")
            )
        # Replacing the repository/sink cannot reset the owner or replay compute.
        restored = RuntimeBindingSessionAuthoritySink(
            DbRuntimeBindingStore(sessions), await store.get(binding.bindingId)
        )
        await complete_skill_turns(
            request=request, sink=restored, driver=driver, inspect_terminal=inspect
        )
        assert len(calls) == (1 if deferred else 2)
