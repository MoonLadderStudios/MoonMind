"""Hermetic portable PR-repair journey (issue #4270, acceptance A9).

Combines in one executing journey without live mutation:

1. a local bare-repo remote fixture with a non-main base branch
   (``release/2.x``) and a PR head branch;
2. the real portable helpers from the active bundle
   (``pr_resolve_finalize`` / ``pr_resolve_full`` classification,
   ``pr_resolve_contract`` finish-mode, ``_shared/publish_evidence.py``,
   ``github_pr_commands`` preflight/dispatch) against a controlled
   hosting API double (httpx MockTransport);
3. assertions for exact-base merge target ``origin/<base>``, publish-evidence
   write-pushed/no-op artifacts over the real Git remote, resolver dispatch
   to the correct specialized skill, and preset ``pr-review-resolve``
   finishMode/reviewProvider forwarding.

Provider-text parsing assertions supplement but do not replace this journey.
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from contextlib import asynccontextmanager

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_BIN = REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "bin"
SHARED_HELPER = REPO_ROOT / ".agents" / "skills" / "_shared" / "publish_evidence.py"
PRESET_PATH = REPO_ROOT / "api_service" / "data" / "presets" / "pr-review-resolve.yaml"

BASE_BRANCH = "release/2.x"
HEAD_BRANCH = "feature/pr-4270"


def _git(workdir: Path, *args: str) -> str:
    return (
        subprocess.check_output(
            ["git", "-C", str(workdir), *args], stderr=subprocess.PIPE
        )
        .decode()
        .strip()
    )


def _load_skill_script(name: str) -> dict[str, Any]:
    return runpy.run_path(str(SKILL_BIN / name))


def _load_publish_evidence() -> Any:
    spec = importlib.util.spec_from_file_location(
        "publish_evidence_portable_journey", SHARED_HELPER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bare_remote_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Local bare remote with a non-main base and a pushed head branch."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.check_call(["git", "init", "--bare", str(origin)])
    work.mkdir()
    _git(work, "init", "-b", BASE_BRANCH)
    _git(work, "config", "user.email", "journey@example.test")
    _git(work, "config", "user.name", "Journey Fixture")
    (work / "app.txt").write_text("base\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "base")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "origin", BASE_BRANCH)
    _git(work, "checkout", "-b", HEAD_BRANCH)
    (work / "app.txt").write_text("base\nhead change\n")
    _git(work, "commit", "-am", "head change")
    _git(work, "push", "origin", HEAD_BRANCH)
    head_sha = _git(work, "rev-parse", "HEAD")
    base_sha = _git(work, "rev-parse", BASE_BRANCH)
    monkeypatch.chdir(work)
    return {
        "origin": origin,
        "work": work,
        "head_sha": head_sha,
        "base_sha": base_sha,
    }


def _controlled_pr_payload(head_sha: str, base_sha: str) -> dict[str, Any]:
    return {
        "number": 4270,
        "url": "https://github.com/example/repo/pull/4270",
        "state": "open",
        "headRefName": HEAD_BRANCH,
        "headRefOid": head_sha,
        "baseRefName": BASE_BRANCH,
        "baseRefOid": base_sha,
    }


def _clean_snapshot(head_sha: str) -> dict[str, Any]:
    return {
        "repository": "example/repo",
        "pr": {
            "number": 4270,
            "url": "https://github.com/example/repo/pull/4270",
            "state": "OPEN",
            "headRefName": HEAD_BRANCH,
            "headRefOid": head_sha,
            "baseRefName": BASE_BRANCH,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
        },
        "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
        "commentsFetch": {"succeeded": True, "source": "controlled-double"},
        "commentsSummary": {
            "includeBotReviewComments": True,
            "hasActionableComments": False,
        },
        "automatedReview": {
            "enabled": True,
            "provider": "codex",
            "freshReviewForHead": True,
            "requestPending": False,
        },
        "progressSignature": f"{head_sha}||",
    }


@pytest.mark.asyncio
async def test_portable_pr_repair_journey_non_main_base(
    bare_remote_fixture: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work: Path = bare_remote_fixture["work"]
    head_sha: str = bare_remote_fixture["head_sha"]
    base_sha: str = bare_remote_fixture["base_sha"]

    # --- 1. Controlled hosting API double serves the exact non-main base. ---
    pr_payload = _controlled_pr_payload(head_sha, base_sha)

    def _handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/example/repo/pulls/4270"
        return httpx.Response(200, json=pr_payload)

    transport = httpx.MockTransport(_handle)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://api.example/repos/example/repo/pulls/4270")
        hosted = response.json()
    assert hosted["baseRefName"] == BASE_BRANCH
    assert hosted["headRefOid"] == head_sha
    merge_target_ref = f"origin/{hosted['baseRefName']}"
    assert merge_target_ref == "origin/release/2.x"

    # --- 2. Real adapter preflight/dispatch preserves the exact base. ---
    from moonmind.workflows.adapters.github_pr_commands import (
        PreflightRequest,
        evaluate_command_preflight,
        handle_verified_command_event,
        resolve_skill_binding,
        verified_command_event_from_mapping,
    )

    preflight = evaluate_command_preflight(
        PreflightRequest(
            skill_id="fix-merge-conflicts",
            pr_base_ref=hosted["baseRefName"],
            pr_base_sha=base_sha,
            pr_head_sha=head_sha,
            is_fork=False,
            fork_write_permitted=False,
            branch_write_authorized=True,
            permissions_revoked=False,
            skill_capability_supported=True,
        )
    )
    assert preflight.ready is True
    assert preflight.merge_target_ref == "origin/release/2.x"

    binding = resolve_skill_binding("fix merge conflicts")
    assert binding is not None and binding.skill_id == "fix-merge-conflicts"
    assert binding.grants_merge_permission is False
    assert binding.requires_publication_evidence is True

    journey = handle_verified_command_event(
        verified_command_event_from_mapping(
            json.loads(
                json.dumps(
                    {
                        "comment_body": "@mm fix merge conflicts",
                        "installation_id": "123",
                        "repository": "example/repo",
                        "pr_number": 4270,
                        "comment_id": "999",
                        "authorization": {
                            "transport_verified": True,
                            "repository_opted_in": True,
                            "actor_authorized": True,
                            "connection_available": True,
                            "budget_available": True,
                            "publication_allowed": True,
                        },
                        "pr_base_ref": BASE_BRANCH,
                        "pr_base_sha": base_sha,
                        "pr_head_sha": head_sha,
                        "branch_write_authorized": True,
                        "skill_capability_supported": True,
                        "skill_snapshot_ref": "snapshot-1",
                        "connection_id": "conn-1",
                        "workflow_ref": "https://workflows.example/runs/1",
                    }
                )
            )
        )
    )
    assert journey.outcome == "dispatch_ready"
    assert journey.dispatch is not None
    assert journey.dispatch.skill_id == "fix-merge-conflicts"
    assert journey.dispatch.merge_target_ref == "origin/release/2.x"

    # --- 3. Real base-sync commands run against the local remote fixture. ---
    fetch = subprocess.run(
        ["git", "fetch", "origin", BASE_BRANCH, "--prune"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert fetch.returncode == 0, fetch.stderr
    merge = subprocess.run(
        ["git", "merge", f"origin/{BASE_BRANCH}"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert merge.returncode == 0, merge.stderr
    assert _git(work, "rev-parse", "HEAD") == head_sha

    # --- 4. Real resolver classification dispatches to the right skill. ---
    finalize = _load_skill_script("pr_resolve_finalize.py")
    full = _load_skill_script("pr_resolve_full.py")
    contract = _load_skill_script("pr_resolve_contract.py")

    blocked_snapshot = _clean_snapshot(head_sha)
    blocked_snapshot["commentsSummary"] = {
        "includeBotReviewComments": True,
        "hasActionableComments": True,
    }
    blocked_snapshot["pr"]["mergeStateStatus"] = "UNSTABLE"
    blocked = finalize["evaluate_finalize_action"](blocked_snapshot)
    assert blocked == {"action": "blocked", "reason": "actionable_comments"}
    full_state = full["evaluate_full_state"](blocked_snapshot)
    assert full_state["status"] == "needs_remediation"
    assert full_state["next_step"] == "run_fix_comments_skill"
    assert contract["remediation_next_step"]("actionable_comments") == (
        "run_fix_comments_skill"
    )
    assert contract["remediation_next_step"]("merge_conflicts") == (
        "run_fix_merge_conflicts_skill"
    )
    assert contract["remediation_next_step"]("ci_failures") == "run_fix_ci_skill"

    clean = finalize["evaluate_finalize_action"](_clean_snapshot(head_sha))
    assert clean == {"action": "merge_now", "reason": "ci_complete"}
    ready = full["evaluate_full_state"](_clean_snapshot(head_sha))
    assert ready["status"] == "ready_for_finalize"
    assert ready["next_step"] == "run_finalize"

    # --- 5. fix_only carries no merge authority on the same validated head. ---
    main = finalize["main"]
    snapshot_path = tmp_path / "snapshot.json"
    result_path = tmp_path / "result.json"
    snapshot_path.write_text(json.dumps(_clean_snapshot(head_sha)), encoding="utf-8")

    def _write_snapshot(_script: Path, _pr: str | None, dest: Path, **_: Any) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(snapshot_path.read_text(encoding="utf-8"), encoding="utf-8")

    merged_calls: list[tuple[str, str]] = []
    monkeypatch.setitem(main.__globals__, "_run_snapshot", _write_snapshot)
    monkeypatch.setitem(
        main.__globals__,
        "_merge_pr",
        lambda selector, method, head: merged_calls.append((selector, method)),
    )
    monkeypatch.delenv("PR_RESOLVER_FINISH_MODE", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--pr",
            "4270",
            "--snapshot-path",
            str(tmp_path / "snapshot.json"),
            "--result-path",
            str(result_path),
            "--review-provider",
            "codex",
            "--require-fresh-review",
            "--finish-mode",
            "fix_only",
            "--strict-exit-codes",
        ],
    )
    with pytest.raises(SystemExit) as fix_only_exit:
        main()
    assert int(fix_only_exit.value.code) == finalize["EXIT_CODE_REVIEW_CLEAN"]
    fix_only_payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert fix_only_payload["status"] == "review_clean"
    assert fix_only_payload["merge_outcome"] == "skipped"
    assert merged_calls == []

    # --- 6. Real publish-evidence helpers verify the exact remote head. ---
    publish = _load_publish_evidence()
    artifacts = tmp_path / "journey-artifacts"
    pushed_path = publish.write_pushed(
        skill_id="fix-merge-conflicts",
        repo="example/repo",
        branch=HEAD_BRANCH,
        artifacts_dir=artifacts,
    )
    pushed = json.loads(pushed_path.read_text(encoding="utf-8"))
    assert pushed["status"] == "verified"
    assert pushed["action"] == "push"
    assert pushed["localHead"] == head_sha
    assert pushed["remoteBranchHead"] == head_sha
    assert pushed["remoteVerified"] is True
    assert pushed["pushed"] is True

    noop_path = publish.write_no_op(
        skill_id="fix-merge-conflicts",
        repo="example/repo",
        branch=HEAD_BRANCH,
        artifacts_dir=artifacts,
    )
    noop = json.loads(noop_path.read_text(encoding="utf-8"))
    assert noop["status"] == "no_op_verified"
    assert noop["pushed"] is False
    assert noop["localHead"] == head_sha

    # --- 7. Preset binding forwards finishMode/reviewProvider (no merge grant). ---
    preset = yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))
    assert preset["slug"] == "pr-review-resolve"
    annotations = preset["annotations"]
    merge_automation = annotations["workflowPublish"]["mergeAutomation"]
    assert "finish_with_pr_resolver" in merge_automation["finishMode"]
    assert "review_provider" in merge_automation["reviewLoop"]["provider"]
    input_schema = annotations["inputSchema"]
    assert input_schema["properties"]["review_provider"]["default"] == "codex"
    assert input_schema["properties"]["finish_with_pr_resolver"]["default"] is False
    inputs_by_name = {entry["name"]: entry for entry in preset["inputs"]}
    assert inputs_by_name["review_provider"]["default"] == "codex"
    assert inputs_by_name["finish_with_pr_resolver"]["default"] is False

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base
    from api_service.services.presets.catalog import PresetCatalogService

    @asynccontextmanager
    async def _catalog_db(path: Path):
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}/preset.db")
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            yield maker
        finally:
            await engine.dispose()

    seed_dir = tmp_path / "presets"
    seed_dir.mkdir(exist_ok=True)
    shutil.copy(PRESET_PATH, seed_dir / PRESET_PATH.name)
    async with _catalog_db(tmp_path) as maker:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)
            await session.commit()
            default_expanded = await service.expand_template(
                slug="pr-review-resolve",
                scope="global",
                scope_ref=None,
                inputs={"pull_request": "4270"},
                context={"repository": "example/repo"},
            )
            merge_expanded = await service.expand_template(
                slug="pr-review-resolve",
                scope="global",
                scope_ref=None,
                inputs={"pull_request": "4270", "finish_with_pr_resolver": True},
                context={"repository": "example/repo"},
            )
    assert default_expanded["publish"]["mergeAutomation"]["finishMode"] == "fix_only"
    assert default_expanded["publish"]["mergeAutomation"]["reviewLoop"]["provider"] == (
        "codex"
    )
    assert merge_expanded["publish"]["mergeAutomation"]["finishMode"] == "merge"
