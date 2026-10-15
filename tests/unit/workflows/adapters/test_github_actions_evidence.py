"""Exact-head Actions evidence crosses real HTTP and archive boundaries."""

import hashlib
import io
import zipfile
from unittest.mock import AsyncMock

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService

HEAD = "a" * 40
REPO = "acme/repo"
STARTED = "2026-10-14T01:00:00Z"


def archive(files=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, content in (files or {"proof/report.json": b'{"tests":12}'}).items():
            zipped.writestr(name, content)
    return output.getvalue()


def fixture(monkeypatch, *, files=None):
    body = archive(files)
    run = {
        "id": 101,
        "workflow_id": 71,
        "path": ".github/workflows/test.yml",
        "head_sha": HEAD,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "run_started_at": STARTED,
        "repository": {"id": 10, "full_name": REPO},
        "head_repository": {"id": 10, "full_name": REPO},
    }
    artifact = {
        "id": 201,
        "name": "test-proof",
        "expired": False,
        "size_in_bytes": len(body),
        "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
        "created_at": "2026-10-14T01:01:00Z",
        "workflow_run": {
            "id": 101,
            "head_sha": HEAD,
            "repository_id": 10,
            "head_repository_id": 10,
        },
    }
    state = {
        "run": run,
        "artifact": artifact,
        "body": body,
        "pulls": 0,
        "download_status": 302,
        "redirect": "https://productionresultssa.blob.core.windows.net/evidence/archive.zip?sig=test",
    }
    requests = []

    def respond(request):
        requests.append(request)
        if state.get("enable_publication"):
            if request.url.path.endswith("/pulls"):
                if request.method == "GET":
                    return httpx.Response(200, json=[])
                assert request.method == "POST"
                state["published"] = True
                return httpx.Response(
                    201,
                    json={
                        "number": 7,
                        "html_url": f"https://github.com/{REPO}/pull/7",
                        "head": {"sha": HEAD},
                        "draft": True,
                    },
                )
            if request.url.path == "/graphql":
                assert request.method == "POST" and state["published"]
                state["marked_ready"] = True
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "markPullRequestReadyForReview": {
                                "pullRequest": {"isDraft": False}
                            }
                        }
                    },
                )
        assert request.method == "GET"
        path = request.url.path
        if request.url.host != "api.github.com":
            assert "authorization" not in request.headers
            if "/job-logs/" in path:
                return httpx.Response(
                    200,
                    content=state.get(
                        "log_bytes", b"compiler.cpp:4: error: unknown type Widget\n"
                    ),
                )
            return httpx.Response(200, content=state["body"])
        assert request.headers["authorization"] == "Bearer " + state.get(
            "token", "admitted-token"
        )
        if path.endswith("/pulls/7"):
            state["pulls"] += 1
            return httpx.Response(
                200,
                json={
                    "number": 7,
                    "state": "closed"
                    if state.get("close_during_collection") and state["pulls"] > 1
                    else "open",
                    "draft": not state.get("marked_ready", False),
                    "node_id": "PR_7",
                    "html_url": state.get(
                        "html_url", f"https://github.com/{REPO}/pull/7"
                    ),
                    "head": {
                        "sha": state.get("moved_head", HEAD)
                        if state["pulls"] > 1
                        else HEAD,
                        "repo": {"id": 10, "full_name": REPO},
                    },
                    "base": {"repo": {"id": 10, "full_name": REPO}},
                },
            )
        if "/commits/" in path:
            return httpx.Response(
                200, json={"sha": HEAD, "commit": {"tree": {"sha": "c" * 40}}}
            )
        if path.endswith("/actions/runs"):
            runs = state.get("runs", [state["run"]])
            return httpx.Response(
                200, json={"total_count": len(runs), "workflow_runs": runs}
            )
        if path.endswith("/attempts/1/jobs"):
            return httpx.Response(
                200,
                json={
                    "total_count": len(state.get("jobs", [])),
                    "jobs": state.get("jobs", []),
                },
            )
        if path.endswith("/actions/jobs/301/logs"):
            return httpx.Response(
                state.get("log_status", 302),
                headers={
                    "Location": state.get(
                        "log_redirect",
                        "https://pipelines.actions.githubusercontent.com/job-logs/301?sig=test",
                    )
                },
            )
        if path.endswith("/check-runs/401/annotations"):
            return httpx.Response(
                state.get("annotations_status", 200),
                json=[
                    {
                        "path": "compiler.cpp",
                        "start_line": 4,
                        "end_line": 4,
                        "annotation_level": "failure",
                        "message": "unknown type Widget",
                    }
                ],
            )
        if path.endswith("/check-runs/401"):
            return httpx.Response(
                200,
                json={
                    "id": 401,
                    "head_sha": state.get("check_head", HEAD),
                    "status": "completed",
                    "conclusion": "failure",
                    "output": {
                        "title": "Compile failed",
                        "summary": "compiler.cpp:4 unknown type Widget",
                        "annotations_count": 1,
                    },
                },
            )
        if path.endswith("/artifacts"):
            artifacts = state.get("artifacts", [state["artifact"]])
            return httpx.Response(
                state.get("artifact_inventory_status", 200),
                json={"total_count": len(artifacts), "artifacts": artifacts},
            )
        if path.endswith("/zip"):
            return httpx.Response(
                state["download_status"], headers={"Location": state["redirect"]}
            )
        if "/actions/runs/" in path:
            run_id = int(path.split("/actions/runs/")[1].split("/")[0])
            if run_id != 101:
                return httpx.Response(
                    200, json=next(run for run in state["runs"] if run["id"] == run_id)
                )
            if "/attempts/" not in path:
                state["run_reads"] = state.get("run_reads", 0) + 1
                if state.get("move_attempt") and state["run_reads"] > 1:
                    return httpx.Response(200, json={**state["run"], "run_attempt": 2})
            return httpx.Response(200, json=state["run"])
        raise AssertionError(str(request.url))

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    service = GitHubService()
    service._repository_reader = AsyncMock(
        return_value=(
            "https://api.github.com",
            {"Authorization": "Bearer admitted-token"},
        )
    )
    return service, state, requests


def failed_job_fixture(monkeypatch):
    service, state, requests = fixture(monkeypatch)
    state["run"]["conclusion"] = "failure"
    state["artifacts"] = []
    state["jobs"] = [
        {
            "id": 301,
            "run_id": 101,
            "run_attempt": 1,
            "head_sha": HEAD,
            "run_url": "https://api.github.com/repos/acme/repo/actions/runs/101",
            "url": "https://api.github.com/repos/acme/repo/actions/jobs/301",
            "check_run_url": "https://api.github.com/repos/acme/repo/check-runs/401",
            "name": "compile",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-10-14T01:00:01Z",
            "completed_at": "2026-10-14T01:01:00Z",
            "steps": [
                {
                    "name": "Compile",
                    "number": 1,
                    "status": "completed",
                    "conclusion": "failure",
                }
            ],
        }
    ]
    return service, state, requests


@pytest.mark.asyncio
async def test_artifactless_failed_job_supplies_real_logs_annotations_and_identity(
    monkeypatch,
):
    import json

    service, state, requests = failed_job_fixture(monkeypatch)
    result = await collect(service)
    assert result["status"] == "ready"
    log = next(item for item in result["files"] if item.get("sourceType") == "job-log")
    assert log["payload"] == b"compiler.cpp:4: error: unknown type Widget\n"
    assert (log["runId"], log["runAttempt"], log["jobId"]) == (101, 1, 301)
    assert log["sha256"] == hashlib.sha256(log["payload"]).hexdigest()
    details = json.loads(
        next(
            item["payload"]
            for item in result["files"]
            if item.get("sourceType") == "job-diagnostics"
        )
    )
    assert details["annotations"][0]["message"] == "unknown type Widget"
    assert details["job"]["steps"][0]["conclusion"] == "failure"
    assert "verdict" not in result and "codeDefect" not in result
    assert any(request.url.path.endswith("/attempts/1/jobs") for request in requests)
    assert state["pulls"] == 2
    repeated = await collect(service)
    assert repeated["semanticDigest"] == result["semanticDigest"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch", ["head", "run", "attempt", "repository", "check", "stale", "redirect"]
)
async def test_job_diagnostics_reject_wrong_identity_or_download_destination(
    monkeypatch, mismatch
):
    service, state, requests = failed_job_fixture(monkeypatch)
    job = state["jobs"][0]
    if mismatch == "head":
        job["head_sha"] = "b" * 40
    elif mismatch == "run":
        job["run_id"] = 999
    elif mismatch == "attempt":
        job["run_attempt"] = 2
    elif mismatch == "repository":
        job["run_url"] = "https://api.github.com/repos/attacker/repo/actions/runs/101"
    elif mismatch == "check":
        state["check_head"] = "b" * 40
    elif mismatch == "stale":
        job["started_at"] = "2026-10-14T00:00:00Z"
    else:
        state["log_redirect"] = "https://attacker.example/steal"
    result = await collect(service)
    assert result["status"] == "unavailable" and result["files"] == []
    assert all(request.url.host != "attacker.example" for request in requests)


@pytest.mark.asyncio
async def test_job_logs_are_independent_of_expired_artifact_and_optional_pending_run(
    monkeypatch,
):
    from copy import deepcopy

    service, state, _requests = failed_job_fixture(monkeypatch)
    state["artifact"]["expired"] = True
    state["artifacts"] = [state["artifact"]]
    optional = deepcopy(state["run"])
    optional.update(id=102, workflow_id=72, status="in_progress", conclusion=None)
    state["runs"] = [state["run"], optional]
    result = await collect(service)
    assert result["status"] == "ready" and result["collectionComplete"] is False
    assert any(item.get("sourceType") == "job-log" for item in result["files"])
    assert result["pendingRunIds"] == [102]
    assert any(item["code"] == "artifact_expired" for item in result["gaps"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denied", ["log_status", "annotations_status", "artifact_inventory_status"]
)
async def test_job_diagnostic_retrieval_gaps_preserve_independent_actual_bytes(
    monkeypatch, denied
):
    service, state, _requests = failed_job_fixture(monkeypatch)
    state[denied] = 403
    result = await collect(service)
    assert result["status"] == "ready" and result["collectionComplete"] is False
    assert any(item.get("sourceType") == "job-diagnostics" for item in result["files"])
    assert any("403" in item["code"] for item in result["gaps"])
    if denied != "log_status":
        assert any(item.get("sourceType") == "job-log" for item in result["files"])


@pytest.mark.asyncio
async def test_job_log_download_is_bounded_without_truncating_required_bytes(
    monkeypatch,
):
    from moonmind.workflows.adapters.github_service import _ACTIONS_JOB_LOG_BYTES

    service, state, _requests = failed_job_fixture(monkeypatch)
    state["log_bytes"] = b"x" * (_ACTIONS_JOB_LOG_BYTES + 1)
    result = await collect(service)
    assert result["status"] == "unavailable" and result["files"] == []
    assert any(item["code"] == "response_size_limit" for item in result["gaps"])


@pytest.mark.asyncio
async def test_failed_job_concurrent_rerun_invalidates_logs(monkeypatch):
    service, state, _requests = failed_job_fixture(monkeypatch)
    state["move_attempt"] = True
    result = await collect(service)
    assert result["status"] == "unavailable" and result["files"] == []


@pytest.mark.asyncio
async def test_unstarted_cancelled_job_does_not_hide_actual_compile_failure(
    monkeypatch,
):
    from copy import deepcopy

    service, state, requests = failed_job_fixture(monkeypatch)
    queued = deepcopy(state["jobs"][0])
    queued.update(
        id=302,
        url="https://api.github.com/repos/acme/repo/actions/jobs/302",
        check_run_url=None,
        started_at=None,
        conclusion="cancelled",
        steps=[],
    )
    state["jobs"].append(queued)
    result = await collect(service)
    assert result["status"] == "ready"
    assert any(
        item.get("jobId") == 301 and item.get("sourceType") == "job-log"
        for item in result["files"]
    )
    assert not any(request.url.path.endswith("/jobs/302/logs") for request in requests)


async def collect(service, **kwargs):
    return await service.read_actions_evidence(
        REPO, 7, HEAD, admitted_workflow_id="work-1", **kwargs
    )


@pytest.mark.asyncio
async def test_collects_actual_bytes_and_exact_identity_without_accepting_green(
    monkeypatch,
):
    service, state, requests = fixture(monkeypatch)
    result = await collect(service)
    assert result["status"] == "ready"
    assert result["headSha"] == HEAD
    assert result["runIds"] == [101]
    assert result["files"][0]["payload"] == b'{"tests":12}'
    assert result["files"][0]["runAttempt"] == 1
    assert result["files"][0]["sha256"] == hashlib.sha256(b'{"tests":12}').hexdigest()
    assert result["evidenceSetDigest"].startswith("sha256:")
    assert "accepted" not in result and "verdict" not in result
    assert state["pulls"] == 2
    service._repository_reader.assert_awaited_once_with(REPO, None, "work-1")
    assert any(request.url.params.get("head_sha") == HEAD for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "wrong_head",
        "wrong_repo",
        "wrong_run",
        "old_attempt",
        "moved_head",
        "wrong_digest",
    ],
)
async def test_untrusted_identity_never_becomes_evidence(monkeypatch, change):
    service, state, _requests = fixture(monkeypatch)
    if change == "wrong_head":
        state["run"]["head_sha"] = "b" * 40
    elif change == "wrong_repo":
        state["run"]["repository"]["full_name"] = "attacker/repo"
    elif change == "wrong_run":
        state["artifact"]["workflow_run"]["id"] = 999
    elif change == "old_attempt":
        state["run"]["run_attempt"] = 2
        state["artifact"]["created_at"] = "2026-10-14T00:00:00Z"
    elif change == "moved_head":
        state["moved_head"] = "b" * 40
    else:
        state["artifact"]["digest"] = "sha256:" + "0" * 64
    result = await collect(service)
    assert result["status"] == "unavailable"
    assert result["files"] == []
    assert result["gaps"]


@pytest.mark.asyncio
async def test_pull_request_url_is_bound_to_the_admitted_connection(monkeypatch):
    enterprise_url = f"https://ghe.example.com/{REPO}/pull/7"
    service, state, _requests = fixture(monkeypatch)
    state["html_url"] = enterprise_url
    accepted = await collect(service, pull_request_url=enterprise_url)
    assert accepted["status"] == "ready"

    rejected = await collect(
        service, pull_request_url=f"https://github.com/{REPO}/pull/7"
    )
    assert rejected["status"] == "unavailable"
    assert rejected["files"] == []
    assert rejected["gaps"] == [{"code": "pull_request_identity_mismatch"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("state_name", ["expired", "403", "410", "pending", "empty"])
async def test_gaps_are_retryable_and_metadata_never_substitutes_bytes(
    monkeypatch, state_name
):
    service, state, _requests = fixture(monkeypatch)
    if state_name == "expired":
        state["artifact"]["expired"] = True
    elif state_name in {"403", "410"}:
        state["download_status"] = int(state_name)
    elif state_name == "pending":
        state["run"]["status"] = "in_progress"
    else:
        state["artifacts"] = []
    result = await collect(service)
    assert result["status"] == ("pending" if state_name == "pending" else "unavailable")
    assert result["retryable"] is True
    assert result["files"] == []
    assert result["evidenceSetDigest"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    ["../secret", "/absolute", "C:/escape", "dir\\escape", "safe/../../escape"],
)
async def test_rejects_unsafe_zip_paths(monkeypatch, filename):
    service, _state, _requests = fixture(monkeypatch, files={filename: b"untrusted"})
    result = await collect(service)
    assert result["status"] == "unavailable"
    assert result["files"] == []


@pytest.mark.asyncio
async def test_rejects_zip_bomb_and_arbitrary_redirect(monkeypatch):
    service, state, requests = fixture(monkeypatch, files={"bomb.txt": b"0" * 1000000})
    assert (await collect(service))["status"] == "unavailable"
    state["redirect"] = "http://169.254.169.254/latest/meta-data"
    requests.clear()
    assert (await collect(service))["status"] == "unavailable"
    assert all(request.url.host == "api.github.com" for request in requests)


@pytest.mark.asyncio
async def test_explicit_source_map_filters_are_enforced(monkeypatch):
    service, _state, requests = fixture(monkeypatch)
    result = await collect(
        service,
        workflow_identities=[".github/workflows/other.yml"],
        artifact_names=["required-proof"],
    )
    assert result["status"] == "unavailable"
    assert result["files"] == []
    assert not any(request.url.path.endswith("/zip") for request in requests)


@pytest.mark.asyncio
async def test_denied_admitted_route_does_not_fall_back(monkeypatch):
    service, _state, requests = fixture(monkeypatch)
    service._repository_reader.side_effect = ValueError("admission unavailable")
    result = await collect(service)
    assert result["status"] == "unavailable"
    assert result["retryable"]
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("moved", [False, True])
async def test_mark_ready_uses_admitted_mutation_and_exact_head(monkeypatch, moved):
    service = GitHubService()
    service._repository_reader = AsyncMock(
        return_value=(
            "https://api.github.com",
            {"Authorization": "Bearer admitted-token"},
        )
    )
    requests = []
    draft = True

    def respond(request):
        nonlocal draft
        requests.append(request)
        if request.method == "POST":
            assert request.url == "https://api.github.com/graphql"
            import json

            body = json.loads(request.content)
            assert "markPullRequestReadyForReview" in body["query"]
            assert body["variables"]["id"] == "PR_7"
            draft = False
            return httpx.Response(
                200,
                json={
                    "data": {
                        "markPullRequestReadyForReview": {
                            "pullRequest": {"isDraft": False}
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "number": 7,
                "node_id": "PR_7",
                "state": "open",
                "draft": draft,
                "head": {"sha": "b" * 40 if moved else HEAD},
                "base": {"repo": {"full_name": REPO}},
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = await service.mark_automation_draft_ready(
        REPO, 7, HEAD, admitted_workflow_id="work-1"
    )
    assert result["ready"] is (not moved)
    assert any(request.method == "POST" for request in requests) is (not moved)
    service._repository_reader.assert_awaited_once_with(
        REPO, None, "work-1", operation="merge_request"
    )


@pytest.mark.asyncio
async def test_actual_frozen_repository_admission_selects_only_its_token(
    monkeypatch, tmp_path
):
    from tests.unit.workflows.adapters.test_github_service_admitted_operations import (
        _record_authority,
    )

    _service, state, requests = fixture(monkeypatch)
    state["token"] = "synthetic-selected-pat"
    engine, reads = await _record_authority(
        monkeypatch, tmp_path, admitted_operations=("read",)
    )
    try:
        result = await GitHubService().read_actions_evidence(
            REPO, 7, HEAD, admitted_workflow_id="mm:run-b"
        )
        assert result["status"] == "ready"
        assert len(reads) == 1
        assert all(
            "synthetic-ambient-pat" not in str(request.headers) for request in requests
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_current_rerun_does_not_reuse_old_artifact_or_block_new_bytes(
    monkeypatch,
):
    from copy import deepcopy

    service, state, _requests = fixture(monkeypatch)
    state["run"]["run_attempt"] = 2
    old = deepcopy(state["artifact"])
    old["id"] = 200
    old["created_at"] = "2026-10-14T00:00:00Z"
    state["artifacts"] = [old, state["artifact"]]
    result = await collect(service, artifact_names=["test-proof"])
    assert result["status"] == "ready"
    assert [item["artifactId"] for item in result["files"]] == [201]
    assert result["excludedArtifacts"][0]["artifactId"] == 200


@pytest.mark.asyncio
async def test_403_clears_when_same_exact_artifact_bytes_become_available(monkeypatch):
    service, state, _requests = fixture(monkeypatch)
    state["download_status"] = 403
    assert (await collect(service))["status"] == "unavailable"
    state["download_status"] = 302
    result = await collect(service)
    assert result["status"] == "ready"
    assert result["gaps"] == []


@pytest.mark.asyncio
async def test_symlink_is_never_materialized(monkeypatch):
    service, state, _requests = fixture(monkeypatch)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        entry = zipfile.ZipInfo("escape")
        entry.create_system = 3
        entry.external_attr = 0o120777 << 16
        zipped.writestr(entry, "../../outside")
    state["body"] = output.getvalue()
    state["artifact"]["digest"] = "sha256:" + hashlib.sha256(state["body"]).hexdigest()
    result = await collect(service)
    assert result["status"] == "unavailable" and result["files"] == []


@pytest.mark.asyncio
async def test_concurrent_rerun_invalidates_downloaded_bytes(monkeypatch):
    service, state, _requests = fixture(monkeypatch)
    state["move_attempt"] = True
    result = await collect(service)
    assert result["status"] == "unavailable" and result["files"] == []
    assert result["semanticDigest"] is None


@pytest.mark.asyncio
async def test_final_pr_read_projects_current_closed_state(monkeypatch):
    service, state, _requests = fixture(monkeypatch)
    state["close_during_collection"] = True
    result = await collect(service)
    assert result["pullRequestOpen"] is False


@pytest.mark.asyncio
async def test_blank_admission_cannot_fall_back_to_deployment_credentials(monkeypatch):
    service, _state, requests = fixture(monkeypatch)
    with pytest.raises(ValueError, match="admitted workflow"):
        await service.read_actions_evidence(REPO, 7, HEAD, admitted_workflow_id="")
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unrelated", ["expired", "pending"])
async def test_partial_usable_bytes_reach_verifier_without_inventing_optional_gates(
    monkeypatch, unrelated
):
    from copy import deepcopy

    service, state, _requests = fixture(monkeypatch)
    if unrelated == "expired":
        other = deepcopy(state["artifact"])
        other.update(id=202, name="optional-diagnostics", expired=True)
        state["artifacts"] = [state["artifact"], other]
    else:
        other = deepcopy(state["run"])
        other.update(id=102, workflow_id=72, status="in_progress", conclusion=None)
        state["runs"] = [state["run"], other]
    result = await collect(service)
    assert result["status"] == "ready"
    assert result["collectionComplete"] is False
    assert result["files"][0]["payload"] == b'{"tests":12}'
    assert result["gaps"] or result["pendingRunIds"] == [102]
