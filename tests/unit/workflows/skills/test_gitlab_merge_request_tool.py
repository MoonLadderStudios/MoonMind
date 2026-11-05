"""The registered GitLab merge-request tool through dispatch, connection and durability.

Every test is hermetic: the GitLab instance is an ``httpx.MockTransport`` and
results persist through a real SQLite-backed Temporal artifact service. Most
tests inject a recorded connection model and a Secrets-System resolver behind
the real bound acquisition; the served-enrollment journey instead records the
connection through the Source Control routes and runs the registered handler
with its production loader and Secrets resolver.
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from moonmind.workflows.executions.repository_contract import (
    REPOSITORY_SETUP_REQUIRED,
    RepositoryConnection,
    RepositoryRouteError,
)
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.gitlab_merge_request import (
    TOOL_NAME,
    GitLabMergeRequestTool,
    register_gitlab_merge_request_tool_handler,
)
from moonmind.workflows.skills.plan_validation import (
    PlanValidationError,
    validate_plan_payload,
)
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.tool_definitions import default_registry_tool_payload
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
from moonmind.workflows.skills.tool_registry import (
    create_registry_snapshot,
    parse_tool_registry,
)
from moonmind.workflows.temporal.activity_catalog import (
    INTEGRATIONS_FLEET,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalSkillActivities,
    _default_skill_registry_payload,
)
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from tests.unit.workflows.temporal.test_remediation_context import temporal_db

ENDPOINT = "https://gitlab.example.com"
PROJECT = "group/subgroup/project"
MR_IID = 7
WORKFLOW_ID = "mm:gitlab-journey"
TOKEN = "glpat-bound-secret"
# httpx reports the decoded path; the wire path keeps the encoded project.
MR_PATH = "/api/v4/projects/group/subgroup/project/merge_requests/7"
MR_RAW_PATH = b"/api/v4/projects/group%2Fsubgroup%2Fproject/merge_requests/7"


def _inputs(**overrides):
    inputs = {"connectionRef": "gitlab-main", "project": PROJECT, "mrIid": MR_IID}
    inputs.update(overrides)
    return inputs


def _recorded_connection(
    operations=("read", "review_request"), **overrides
) -> RepositoryConnection:
    payload = {
        "schemaVersion": "moonmind.repository-connection.v1",
        "id": "gitlab-main",
        "provider": "git",
        "displayName": "GitLab",
        "endpointRef": ENDPOINT,
        "allowedOperations": list(operations),
        "clientPolicy": {
            "pinnedVersion": "2.45.0",
            "toolBundleRef": "tool-bundle:git",
            "executableSha256": "a" * 64,
        },
        "credential": {
            "source": "secret_ref",
            "credentialRef": {"provider": "db", "key": "GITLAB_TOKEN"},
        },
        "hostingService": "generic_git",
        "ownership": {"ownerRef": "system:operator", "scopeType": "system"},
    }
    payload.update(overrides)
    return RepositoryConnection.model_validate(payload)


class _Secrets:
    def __init__(self) -> None:
        self.reads: list[str] = []

    async def __call__(self, ref: str) -> str:
        self.reads.append(ref)
        return TOKEN


class _GitLab:
    """Hermetic GitLab instance recording every request it receives."""

    def __init__(self, *, notes=(), note_pages=1, per_page=20) -> None:
        self.requests: list[httpx.Request] = []
        # Project paths this instance reports, for assignment verification.
        self.projects = {PROJECT: 99, "group/subgroup/docs": 100}
        self.notes = list(notes)
        self.note_pages = note_pages
        self.per_page = per_page
        self.post_status = 201
        self.status_for: dict[str, int] = {}

    @property
    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["PRIVATE-TOKEN"] == TOKEN
        path = request.url.path
        if path in self.status_for:
            return httpx.Response(self.status_for[path], json={"message": "x"})
        project = path.removeprefix("/api/v4/projects/")
        if request.method == "GET" and project in self.projects:
            return httpx.Response(
                200,
                json={"id": self.projects[project], "path_with_namespace": project},
            )
        if request.method == "POST":
            body = json.loads(request.content)["body"]
            note = {"id": 4999 + len(self.posts), "body": body}
            # The note lands even when its acknowledgment is lost.
            self.notes.insert(0, note)
            return httpx.Response(self.post_status, json=note)
        addressed, mr, route = project.partition(f"/merge_requests/{MR_IID}")
        # A numeric ID addresses that project wherever its path now leads.
        project_id = (
            int(addressed) if addressed.isdigit() else self.projects.get(addressed)
        )
        if not mr or project_id is None:
            return httpx.Response(404, json={"message": "not found"})
        if route == "":
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "iid": MR_IID,
                    "project_id": project_id,
                    "title": "Small slice",
                    "state": "opened",
                    "source_branch": "feature",
                    "target_branch": "main",
                    "sha": "abc123",
                    "detailed_merge_status": "mergeable",
                    "has_conflicts": False,
                    "web_url": f"{ENDPOINT}/{PROJECT}/-/merge_requests/7",
                },
            )
        if route == "/discussions":
            return httpx.Response(200, json=[{"id": "d1", "notes": []}])
        if route == "/approvals":
            return httpx.Response(200, json={"approved": False})
        if route == "/pipelines":
            return httpx.Response(200, json=[{"id": 5, "status": "success"}])
        if route == "/notes":
            page = int(request.url.params.get("page", "1"))
            start = (page - 1) * self.per_page
            headers = {"X-Next-Page": str(page + 1)} if page < self.note_pages else {}
            return httpx.Response(
                200, json=self.notes[start : start + self.per_page], headers=headers
            )
        return httpx.Response(404, json={"message": "not found"})


def _tool(gitlab: _GitLab, secrets: _Secrets, *, connection=None, load=None):
    connection = connection or _recorded_connection()
    loaded: list[tuple[str, str]] = []

    async def _load(connection_ref: str, repository: str, *, workflow_id: str):
        loaded.append((connection_ref, repository, workflow_id))
        if load is not None:
            return await load(connection_ref, repository)
        return connection, "principal:admitted", None

    tool = GitLabMergeRequestTool(
        load_connection=_load,
        resolve_secret=secrets,
        http_transport=httpx.MockTransport(gitlab),
    )
    tool.loaded = loaded  # type: ignore[attr-defined]
    return tool


def _snapshot(inputs):
    parameters = {
        "workflow": {
            "instructions": "Read the merge request and post the summary note.",
            "steps": [
                {
                    "id": "gitlab",
                    "type": "tool",
                    "tool": {"id": TOOL_NAME, "inputs": inputs},
                }
            ],
        }
    }
    snapshot = create_registry_snapshot(
        skills=parse_tool_registry(
            _default_skill_registry_payload(parameters=parameters)
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    return parameters, snapshot


async def _dispatch(
    tool,
    inputs,
    service,
    *,
    attempt=None,
    workflow_id=WORKFLOW_ID,
    run_id="run-1",
    principal="system:workflow-owner",
):
    """One mm.tool.execute attempt through the worker's real dispatch owner.

    ``attempt`` runs the activity itself as that Temporal retry attempt.
    """

    dispatcher = SkillActivityDispatcher()
    register_gitlab_merge_request_tool_handler(dispatcher, tool=tool)
    _parameters, snapshot = _snapshot(inputs)
    activities = TemporalSkillActivities(
        dispatcher=dispatcher, artifact_service=service
    )
    invocation = {
        "invocation_payload": {
            "id": "gitlab",
            "tool": {"type": "skill", "name": TOOL_NAME},
            "inputs": inputs,
        },
        "registry_snapshot": snapshot,
        "principal": principal,
        "context": {
            "namespace": "default",
            "workflow_id": workflow_id,
            "run_id": run_id,
            "node_id": "gitlab",
        },
    }
    if attempt is None:
        return await activities._execute_skill_invocation(**invocation)
    environment = ActivityEnvironment()
    environment.info = dataclasses.replace(environment.info, attempt=attempt)
    return await environment.run(activities.mm_tool_execute, **invocation)


async def _records(service, *, workflow_id=WORKFLOW_ID, run_id="run-1") -> list[dict]:
    access = ArtifactAccessContext.for_execution(workflow_id)
    artifacts = await service.list_for_execution(
        namespace="default",
        workflow_id=workflow_id,
        run_id=run_id,
        link_type="output.primary",
        access=access,
    )
    records = []
    for artifact in artifacts:
        _artifact, body = await service.read(
            artifact_id=artifact.artifact_id, access=access
        )
        records.append(json.loads(body))
    return records


def _service(session, tmp_path) -> TemporalArtifactService:
    return TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=LocalTemporalArtifactStore(tmp_path / "blobs"),
    )


# ---------------------------------------------------------------------------
# Registration and admission
# ---------------------------------------------------------------------------


def test_definition_routes_to_integrations_and_advertises_only_reads_and_note():
    definition = parse_tool_definition(default_registry_tool_payload(name=TOOL_NAME))
    route = build_default_activity_catalog().resolve_skill(definition)

    assert route.activity_type == "mm.tool.execute"
    assert route.fleet == INTEGRATIONS_FLEET
    assert definition.required_capabilities == ("integration:gitlab",)
    schema = definition.input_schema
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "connectionRef",
        "endpoint",
        "project",
        "mrIid",
        "read",
        "note",
    }
    assert schema["properties"]["read"]["items"]["enum"] == [
        "mr",
        "discussions",
        "status",
    ]
    assert set(schema["properties"]["note"]["properties"]) == {"body", "operationId"}
    # Retrying the activity reconciles the footer before any repeat mutation.
    assert definition.policies.retries.max_attempts > 1


def test_admitted_plan_accepts_reads_and_note_and_rejects_unsupported_operations():
    inputs = _inputs(note={"body": "Summary"})
    parameters, snapshot = _snapshot(inputs)
    plan = _build_runtime_planner()(None, parameters, snapshot)

    admitted = validate_plan_payload(payload=plan, registry_snapshot=snapshot)
    assert admitted.plan.nodes[0].inputs == inputs

    for unsupported in (_inputs(merge=True), _inputs(read=["inline"])):
        parameters, snapshot = _snapshot(unsupported)
        plan = _build_runtime_planner()(None, parameters, snapshot)
        with pytest.raises(PlanValidationError):
            validate_plan_payload(payload=plan, registry_snapshot=snapshot)


def test_worker_dispatcher_registers_the_trusted_handler():
    dispatcher = SkillActivityDispatcher()
    register_gitlab_merge_request_tool_handler(dispatcher)

    assert dispatcher.registered_skill_handler(skill_name=TOOL_NAME) is not None


# ---------------------------------------------------------------------------
# Admitted read/note journey
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_invocation_reads_and_posts_one_note_with_durable_result(
    tmp_path,
):
    gitlab, secrets = _GitLab(), _Secrets()
    tool = _tool(gitlab, secrets)
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        result = await _dispatch(tool, _inputs(note={"body": "Summary"}), service)
        records = await _records(service)

    assert result.status == "COMPLETED"
    outputs = result.outputs
    assert outputs["target"] == {
        "connectionRef": "gitlab-main",
        "endpoint": ENDPOINT,
        "project": PROJECT,
        "mrIid": MR_IID,
    }
    assert outputs["mr"]["title"] == "Small slice"
    assert outputs["discussions"] == {"count": 1, "complete": True}
    assert outputs["mrStatus"]["mergeableKnown"] is True
    assert outputs["readsComplete"] is True
    note = outputs["note"]
    assert note["deliveryState"] == "delivered"
    assert note["noteId"] == 5000
    assert tool.loaded == [("gitlab-main", PROJECT, WORKFLOW_ID)]
    assert secrets.reads == ["db://GITLAB_TOKEN"]
    assert len(gitlab.posts) == 1
    assert gitlab.posts[0].url.raw_path == MR_RAW_PATH + b"/notes"
    posted = json.loads(gitlab.posts[0].content)["body"]
    assert posted.startswith("Summary\n\n")
    assert f"operation:{note['operationId']}" in posted

    assert [record["note"]["deliveryState"] for record in records] == ["delivered"]
    record = records[0]
    assert outputs["resultArtifactRef"]
    assert record["note"]["operationId"] == note["operationId"]
    assert record["reads"]["discussions"]["discussions"] == [{"id": "d1", "notes": []}]
    assert TOKEN not in json.dumps(record)
    assert TOKEN not in json.dumps(outputs)


@pytest.mark.asyncio
async def test_operation_identity_is_the_logical_step_and_admitted_body(tmp_path):
    gitlab, secrets = _GitLab(), _Secrets()
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        first = await _dispatch(
            _tool(gitlab, secrets), _inputs(read=[], note={"body": "A"}), service
        )
        retried = await _dispatch(
            _tool(gitlab, secrets), _inputs(read=[], note={"body": "A"}), service
        )
        edited = await _dispatch(
            _tool(gitlab, secrets), _inputs(read=[], note={"body": "B"}), service
        )

    assert (
        retried.outputs["note"]["operationId"] == first.outputs["note"]["operationId"]
    )
    assert retried.outputs["note"]["deliveryState"] == "reconciled"
    assert edited.outputs["note"]["operationId"] != first.outputs["note"]["operationId"]
    assert len(gitlab.posts) == 2


# ---------------------------------------------------------------------------
# Bounded and observable failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_authority_cannot_post_and_reads_no_secret(tmp_path):
    gitlab, secrets = _GitLab(), _Secrets()
    tool = _tool(gitlab, secrets, connection=_recorded_connection(operations=("read",)))
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        result = await _dispatch(tool, _inputs(note={"body": "Summary"}), service)
        records = await _records(service)

    assert result.status == "FAILED"
    assert result.outputs["error"]["code"] == "BOUND_DENIED"
    assert result.outputs["note"]["deliveryState"] == "not_attempted"
    assert secrets.reads == []
    assert gitlab.requests == []
    assert records[0]["outcome"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inputs", "connection", "code"),
    [
        (
            _inputs(endpoint="https://gitlab-other.example.com"),
            None,
            "gitlab_identity_rejected",
        ),
        (
            _inputs(),
            _recorded_connection(hostingService="github"),
            "gitlab_connection_unsupported",
        ),
        (
            _inputs(),
            _recorded_connection(endpointRef="https://10.0.0.5"),
            "gitlab_identity_rejected",
        ),
    ],
)
async def test_endpoint_and_connection_mismatch_fail_before_secrets(
    tmp_path, inputs, connection, code
):
    gitlab, secrets = _GitLab(), _Secrets()
    tool = _tool(gitlab, secrets, connection=connection)
    async with temporal_db(tmp_path) as session:
        result = await _dispatch(tool, inputs, _service(session, tmp_path))

    assert result.status == "FAILED"
    assert result.outputs["error"]["code"] == code
    assert secrets.reads == []
    assert gitlab.requests == []


@pytest.mark.asyncio
async def test_unassigned_project_fails_observably_before_secrets(tmp_path):
    async def _unassigned(connection_ref, repository):
        raise RepositoryRouteError(
            REPOSITORY_SETUP_REQUIRED,
            f"repository connection {connection_ref} is not assigned to {repository}",
        )

    gitlab, secrets = _GitLab(), _Secrets()
    tool = _tool(gitlab, secrets, load=_unassigned)
    async with temporal_db(tmp_path) as session:
        result = await _dispatch(tool, _inputs(), _service(session, tmp_path))

    assert result.status == "FAILED"
    assert result.outputs["error"]["code"] == REPOSITORY_SETUP_REQUIRED
    assert "not assigned" in result.outputs["error"]["message"]
    assert secrets.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project", "admitted_id"), [("99", "100"), (PROJECT, "not-a-project-id")]
)
async def test_mismatched_admitted_project_id_fails_before_secrets(
    tmp_path, project, admitted_id
):
    async def _admitted(_ref, _repo):
        return _recorded_connection(), "principal:admitted", admitted_id

    gitlab, secrets = _GitLab(), _Secrets()
    async with temporal_db(tmp_path) as session:
        result = await _dispatch(
            _tool(gitlab, secrets, load=_admitted),
            _inputs(project=project, read=["mr"]),
            _service(session, tmp_path),
        )

    assert result.status == "FAILED"
    assert result.outputs["error"]["code"] == "gitlab_identity_rejected"
    assert secrets.reads == []
    assert gitlab.requests == []


@pytest.mark.asyncio
async def test_cross_instance_redirect_and_expired_credential_are_bounded(tmp_path):
    def _redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    redirecting = _GitLab()
    expired = _GitLab()
    expired.status_for[MR_PATH] = 401
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        tool = GitLabMergeRequestTool(
            load_connection=lambda _ref, _repo, **_run: _async(
                (_recorded_connection(), "principal:admitted", None)
            ),
            resolve_secret=_Secrets(),
            http_transport=httpx.MockTransport(
                lambda request: (
                    redirecting.requests.append(request),
                    _redirect(request),
                )[1]
            ),
        )
        redirected = await _dispatch(tool, _inputs(note={"body": "x"}), service)
        expired_result = await _dispatch(
            _tool(expired, _Secrets()), _inputs(note={"body": "x"}), service
        )

    assert redirected.status == "FAILED"
    assert redirected.outputs["error"]["code"] == "gitlab_identity_rejected"
    assert [r.url.host for r in redirecting.requests] == ["gitlab.example.com"]
    assert expired_result.status == "FAILED"
    assert expired_result.outputs["error"]["code"] == "gitlab_token_expired"
    # A first attempt stopped by its reads never reached the note.
    assert expired_result.outputs["note"]["deliveryState"] == "not_attempted"
    assert expired.posts == []


@pytest.mark.asyncio
async def test_expired_credential_during_note_reconciliation_stays_visible(tmp_path):
    expired = _GitLab()
    expired.status_for[f"{MR_PATH}/notes"] = 401
    async with temporal_db(tmp_path) as session:
        result = await _dispatch(
            _tool(expired, _Secrets()),
            _inputs(read=[], note={"body": "x"}),
            _service(session, tmp_path),
        )

    assert result.status == "FAILED"
    assert result.outputs["error"]["code"] == "gitlab_token_expired"
    # A refused footer lookup cannot show that a restarted step's note is absent.
    note = result.outputs["note"]
    assert note["deliveryState"] == "uncertain"
    assert note["reason"] == "note_lookup_refused"
    next_action = note["automationHandoff"]["nextAction"]
    assert next_action["inputs"]["note"]["operationId"] == note["operationId"]
    assert expired.posts == []


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_partial_status_is_reported_incomplete(tmp_path):
    gitlab = _GitLab()
    original = gitlab.__call__

    def _paged_pipelines(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"{MR_PATH}/pipelines":
            gitlab.requests.append(request)
            page = request.url.params.get("page", "1")
            return httpx.Response(
                200,
                json=[{"id": int(page), "status": "running"}],
                headers={"X-Next-Page": str(int(page) + 1)},
            )
        return original(request)

    tool = GitLabMergeRequestTool(
        load_connection=lambda _ref, _repo, **_run: _async(
            (_recorded_connection(), "principal:admitted", None)
        ),
        resolve_secret=_Secrets(),
        http_transport=httpx.MockTransport(_paged_pipelines),
    )
    async with temporal_db(tmp_path) as session:
        result = await _dispatch(
            tool, _inputs(read=["status"]), _service(session, tmp_path)
        )

    assert result.status == "COMPLETED"
    assert result.outputs["readsComplete"] is False
    assert result.outputs["mrStatus"]["complete"] is False


# ---------------------------------------------------------------------------
# Lost acknowledgment, restart, and exhausted reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lost_ack_and_restart_reconcile_page_two_footer_without_duplicate(
    tmp_path,
):
    gitlab = _GitLab(notes=[{"id": 100 + i, "body": "older"} for i in range(5)])
    gitlab.post_status = 502
    inputs = _inputs(read=[], note={"body": "Summary"})
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        with pytest.raises(ApplicationError) as lost:
            await _dispatch(_tool(gitlab, _Secrets()), inputs, service)
        assert lost.value.non_retryable is False
        # Twenty newer notes push the landed footer onto page two before the
        # retried activity runs on a fresh worker, handler, and adapter.
        gitlab.notes = [
            {"id": 900 + i, "body": "newer"} for i in range(20)
        ] + gitlab.notes
        gitlab.note_pages = 2
        retried = await _dispatch(_tool(gitlab, _Secrets()), inputs, service)
        records = await _records(service)

    assert len(gitlab.posts) == 1
    assert retried.status == "COMPLETED"
    assert retried.outputs["note"]["deliveryState"] == "reconciled"
    assert retried.outputs["note"]["noteId"] == 5000
    pages = [
        r.url.params.get("page")
        for r in gitlab.requests
        if r.method == "GET" and r.url.path.endswith("/notes")
    ]
    assert pages[-2:] == ["1", "2"]
    states = sorted(record["note"]["deliveryState"] for record in records)
    assert states == ["reconciled", "uncertain"]
    uncertain = next(r for r in records if r["note"]["deliveryState"] == "uncertain")
    assert uncertain["note"]["reason"] == "acknowledgment_lost"
    handoff = uncertain["note"]["automationHandoff"]
    assert handoff["nextAction"]["inputs"]["note"]["operationId"] == (
        uncertain["note"]["operationId"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refusal", "code"),
    [("expired", "gitlab_token_expired"), ("redirect", "gitlab_identity_rejected")],
)
async def test_lost_ack_then_refused_lookup_keeps_the_landed_note_uncertain(
    tmp_path, refusal, code
):
    gitlab = _GitLab()
    gitlab.post_status = 502

    def _refuse_lookup_after_post(request: httpx.Request) -> httpx.Response:
        if gitlab.posts and request.url.path == f"{MR_PATH}/notes":
            gitlab.requests.append(request)
            if refusal == "expired":
                return httpx.Response(401, json={"message": "expired"})
            return httpx.Response(302, headers={"location": "https://evil.example/n"})
        return gitlab(request)

    inputs = _inputs(read=[], note={"body": "Summary"})
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        with pytest.raises(ApplicationError) as lost:
            await _dispatch(
                _tool(_refuse_lookup_after_post, _Secrets()), inputs, service
            )
        assert lost.value.non_retryable is False
        # Temporal's retry runs on a fresh worker, handler, and adapter.
        retried = await _dispatch(
            _tool(_refuse_lookup_after_post, _Secrets()), inputs, service, attempt=2
        )
        records = await _records(service)

    # The first POST landed; the refused lookup authorizes no second one.
    assert [note["id"] for note in gitlab.notes] == [5000]
    assert len(gitlab.posts) == 1
    assert retried.status == "FAILED"
    assert retried.outputs["error"]["code"] == code
    note = retried.outputs["note"]
    assert note["deliveryState"] == "uncertain"
    assert note["reason"] == "note_lookup_refused"
    handoff = note["automationHandoff"]
    assert handoff["operation_id"] == note["operationId"]
    assert handoff["nextAction"]["tool"] == TOOL_NAME
    assert handoff["nextAction"]["inputs"]["note"]["operationId"] == note["operationId"]
    record = next(r for r in records if r["error"] is not None)
    assert record["outcome"] == "uncertain"
    assert record["note"]["operationId"] == note["operationId"]
    assert record["note"]["body"] == "Summary"


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", ["activity_retry", "explicit_operation"])
async def test_retried_operation_stopped_before_lookup_stays_uncertain(
    tmp_path, continuation
):
    gitlab = _GitLab()
    gitlab.post_status = 502
    inputs = _inputs(read=["mr"], note={"body": "Summary"})
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        with pytest.raises(ApplicationError):
            await _dispatch(_tool(gitlab, _Secrets()), inputs, service)
        (first,) = await _records(service)
        operation_id = first["note"]["operationId"]
        gitlab.status_for[MR_PATH] = 401
        if continuation == "activity_retry":
            retried = await _dispatch(
                _tool(gitlab, _Secrets()), inputs, service, attempt=2
            )
        else:
            # The handoff's continuation, from the durable result record.
            retried = await _dispatch(
                _tool(gitlab, _Secrets()),
                _inputs(
                    read=["mr"],
                    note={"body": first["note"]["body"], "operationId": operation_id},
                ),
                service,
            )
        records = await _records(service)

    assert len(gitlab.posts) == 1
    assert retried.status == "FAILED"
    assert retried.outputs["error"]["code"] == "gitlab_token_expired"
    note = retried.outputs["note"]
    assert note["operationId"] == operation_id
    assert note["deliveryState"] == "uncertain"
    assert note["reason"] == "reconciliation_not_reached"
    assert note["automationHandoff"]["nextAction"]["inputs"]["note"] == {
        "operationId": operation_id
    }
    record = next(r for r in records if r["error"] is not None)
    assert record["outcome"] == "uncertain"
    assert record["note"]["body"] == "Summary"


@pytest.mark.asyncio
async def test_exhausted_pagination_preserves_operation_and_next_action(tmp_path):
    gitlab = _GitLab(
        notes=[{"id": i, "body": "unrelated"} for i in range(2000)],
        note_pages=1000,
        per_page=100,
    )
    inputs = _inputs(read=[], note={"body": "Summary"})
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        result = await _dispatch(_tool(gitlab, _Secrets()), inputs, service)
        records = await _records(service)

    assert gitlab.posts == []
    assert result.status == "FAILED"
    note = result.outputs["note"]
    assert note["deliveryState"] == "uncertain"
    handoff = note["automationHandoff"]
    assert handoff["operation_id"] == note["operationId"]
    assert handoff["reason"] == "note_lookup_exhausted"
    assert handoff["nextAction"]["tool"] == TOOL_NAME
    assert handoff["nextAction"]["inputs"]["note"]["operationId"] == note["operationId"]
    assert "human" not in json.dumps(handoff).lower()
    record = records[0]
    assert record["outcome"] == "uncertain"
    # The exact admitted work survives for the automated continuation.
    assert record["note"]["body"] == "Summary"


# ---------------------------------------------------------------------------
# Served enrollment, verified assignment, and the production loader
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def served(tmp_path, monkeypatch):
    """The served Source Control and execution routes on one database.

    Connections are enrolled and assigned, and runs submitted, through their
    routes under operator admission; a real ``TemporalExecutionService``
    records each run (only the Temporal start is mocked). The tool then runs
    for that run under its execution-scoped principal, reading the same
    database through the production admission reader and Secrets resolver,
    and every GitLab client reaches only the hermetic instance.
    """

    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    from api_service.api.routers import executions
    from api_service.api.routers import repository_connections as routes
    from api_service.db import base as db_base
    from api_service.db.models import Base
    from api_service.services.secrets import SecretsService
    from api_service.services.settings_catalog import SettingsCatalogService
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.service import TemporalExecutionService
    from tests.unit.api.test_executions_temporal import _override_user_dependencies

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/connections.db", future=True
    )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(db_base, "async_session_maker", maker)
    monkeypatch.setattr(
        "moonmind.auth.resolvers.db_resolver.async_session_maker", maker
    )
    monkeypatch.setattr(
        "moonmind.auth.github_app_setup._deployment_git_client_policy",
        lambda: {
            "pinnedVersion": "2.46.0",
            "toolBundleRef": "repository-client:git-system",
            "executableSha256": "sha256:git",
        },
    )
    # A tool-only run needs no agent runtime, saved settings, or object store.
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "submitted")
    )

    async def _no_overrides(_service, *, keys, **_kwargs):
        return {}

    monkeypatch.setattr(SettingsCatalogService, "_get_overrides", _no_overrides)
    secret_reads: list[str] = []
    read_secret = SecretsService.get_secret

    async def _read_secret(_cls, session, slug, **kwargs):
        secret_reads.append(slug)
        return await read_secret(session, slug, **kwargs)

    monkeypatch.setattr(SecretsService, "get_secret", classmethod(_read_secret))

    temporal = MagicMock()
    temporal.start_workflow = AsyncMock(
        side_effect=lambda **_kwargs: SimpleNamespace(run_id=uuid4().hex)
    )
    temporal.namespace_retention_seconds = AsyncMock(return_value=86400)
    temporal.describe_workflow = AsyncMock(return_value=None)

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/repository-connections")
    app.include_router(executions.router)

    async def _session():
        async with maker() as session:
            yield session

    async def _service(session=Depends(db_base.get_async_session)):
        return TemporalExecutionService(session, client_adapter=temporal)

    _override_user_dependencies(app, is_superuser=False)
    app.dependency_overrides[db_base.get_async_session] = _session
    app.dependency_overrides[executions._get_service] = _service
    app.dependency_overrides[executions.get_temporal_client] = lambda: AsyncMock()

    gitlab = _GitLab()
    real_client = httpx.AsyncClient
    async with real_client(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_client(
                transport=httpx.MockTransport(gitlab), **kwargs
            ),
        )
        yield SimpleNamespace(
            client=client,
            gitlab=gitlab,
            secret_reads=secret_reads,
            maker=maker,
            temporal=temporal,
        )
    await engine.dispose()


async def _enroll_and_assign(client, assignments):
    created = await client.post(
        "/api/v1/repository-connections/pat",
        json={
            "requestId": "req-gitlab",
            "connectionId": "gitlab-main",
            "displayName": "GitLab",
            "token": TOKEN,
            "hostingService": "generic_git",
            "endpointRef": ENDPOINT,
            "allowedOperations": ["read", "review_request"],
        },
    )
    assert created.status_code == 201, created.text
    assert TOKEN not in created.text
    view = created.json()
    for index, (project, operations) in enumerate(assignments):
        view = await _assign(client, project, operations, request_id=f"req-{index}")
    return view


async def _assign(client, project, operations, *, request_id, revision=1):
    assigned = await client.post(
        "/api/v1/repository-connections/gitlab-main/assignments",
        json={
            "requestId": request_id,
            "repository": project,
            "operations": list(operations),
            "revision": revision,
        },
    )
    assert assigned.status_code == 200, assigned.text
    return assigned.json()


async def _submit(client, *inputs_list):
    """Submit one run whose steps are GitLab tool steps; return the response."""

    return await client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Read the merge request and post the summary.",
                    "steps": [
                        {
                            "id": f"gitlab-{index}",
                            "type": "tool",
                            "tool": {"id": TOOL_NAME, "inputs": inputs},
                        }
                        for index, inputs in enumerate(inputs_list)
                    ],
                }
            },
        },
    )


async def _recorded_run(maker, workflow_id):
    from api_service.db.models import TemporalExecutionCanonicalRecord

    async with maker() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
    assert record is not None
    return record


async def _run_step(served, tmp_path, inputs):
    """Submit a run, then execute its GitLab step as the run's Activity would."""

    submitted = await _submit(served.client, inputs)
    assert submitted.status_code == 201, submitted.text
    record = await _recorded_run(served.maker, submitted.json()["workflowId"])
    return record, await _execute_for(record, tmp_path, inputs)


async def _execute_for(record, tmp_path, inputs):
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        result = await _dispatch(
            GitLabMergeRequestTool(),
            inputs,
            service,
            workflow_id=record.workflow_id,
            run_id=record.run_id,
            # The execution-scoped principal ``MoonMind.UserWorkflow`` uses.
            principal=f"workflow:{record.workflow_id}",
        )
        records = await _records(
            service, workflow_id=record.workflow_id, run_id=record.run_id
        )
    return result, records


@pytest.mark.asyncio
async def test_served_enrollment_submission_and_run_reach_the_registered_tool(
    served, tmp_path
):
    view = await _enroll_and_assign(
        served.client, [(PROJECT, ("read", "review_request"))]
    )

    assert view["hostingService"] == "generic_git"
    assert view["endpoint"] == ENDPOINT
    assert view["credentialKind"] == "personal_access_token"
    assert view["assignments"] == [
        {
            "repository": PROJECT,
            "providerRepoId": "99",
            "operations": ["read", "review_request"],
            "revision": 1,
            "verified": True,
        }
    ]
    # The assignment was verified with the connection's own token.
    (lookup,) = served.gitlab.requests
    assert lookup.method == "GET"
    assert lookup.url.raw_path == b"/api/v4/projects/group%2Fsubgroup%2Fproject"
    slug = "repository-connection/gitlab-main/credential-1"
    assert served.secret_reads == [slug]

    record, (result, records) = await _run_step(
        served, tmp_path, _inputs(note={"body": "Summary"})
    )

    # Submission recorded what its admitted principal may use; the run's
    # Activity carried that admission, not its own workflow principal.
    from api_service.auth_providers import instance_operator_principal

    assert record.parameters["repositoryToolAdmissions"] == [
        {
            "tool": TOOL_NAME,
            "connectionRef": "gitlab-main",
            "repository": PROJECT,
            "principalRef": instance_operator_principal(),
            "scopeType": "system",
            "scopeRef": None,
            "providerRepoId": "99",
            "assignmentRevision": 1,
        }
    ]
    assert result.status == "COMPLETED", result.outputs
    assert result.outputs["mr"]["title"] == "Small slice"
    assert result.outputs["readsComplete"] is True
    note = result.outputs["note"]
    assert note["deliveryState"] == "delivered"
    assert len(served.gitlab.posts) == 1
    assert (
        f"operation:{note['operationId']}"
        in json.loads(served.gitlab.posts[0].content)["body"]
    )
    assert served.secret_reads == [slug, slug]
    (persisted,) = records
    assert result.outputs["resultArtifactRef"]
    assert persisted["execution"]["workflowId"] == record.workflow_id
    assert persisted["note"]["deliveryState"] == "delivered"
    assert TOKEN not in json.dumps(persisted)


async def _seed_foreign_connection(maker):
    """A connection recorded for another principal, assigned to the project."""

    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.workflows.executions.repository_contract import (
        RepositoryAssignment,
        RepositoryIdentity,
    )

    foreign = ("principal:foreign", ("system", None))
    connection = _recorded_connection(
        id="gitlab-foreign",
        ownership={
            "ownerRef": foreign[0],
            "scopeType": "system",
            "allowedPrincipalRefs": [foreign[0]],
        },
    )
    async with maker() as session:
        service = RepositoryConnectionService(session)
        await service.create_connection(
            connection,
            actor_ref=foreign[0],
            request_id="seed-foreign",
            principal_ref=foreign[0],
            principal_scope=foreign[1],
        )
        await service.set_assignment(
            RepositoryAssignment(
                connectionId="gitlab-foreign",
                identity=RepositoryIdentity(
                    endpoint=ENDPOINT, providerRepoId="99", displayName=PROJECT
                ),
                operations=("read",),
                verified=True,
            ),
            actor_ref=foreign[0],
            request_id="seed-foreign-assignment",
            principal_ref=foreign[0],
            principal_scope=foreign[1],
        )


@pytest.mark.asyncio
async def test_unadmitted_principals_and_selections_are_refused_at_submission(
    served,
):
    await _enroll_and_assign(served.client, [(PROJECT, ("read",))])
    await _seed_foreign_connection(served.maker)

    # The operator may not use a connection recorded for another principal.
    foreign = await _submit(served.client, _inputs(connectionRef="gitlab-foreign"))
    unassigned = await _submit(
        served.client, _inputs(project="group/subgroup/docs", read=["mr"])
    )
    unknown = await _submit(served.client, _inputs(connectionRef="gitlab-other"))

    for refused in (foreign, unassigned, unknown):
        assert refused.status_code == 422, refused.text
        assert TOOL_NAME in refused.json()["detail"]["message"]
    assert "not admitted" in foreign.json()["detail"]["message"]
    assert "not assigned" in unassigned.json()["detail"]["message"]
    assert "not recorded" in unknown.json()["detail"]["message"]
    served.temporal.start_workflow.assert_not_awaited()
    async with served.maker() as session:
        from sqlalchemy import func, select

        from api_service.db.models import TemporalExecutionCanonicalRecord

        runs = (
            await session.execute(
                select(func.count()).select_from(TemporalExecutionCanonicalRecord)
            )
        ).scalar_one()
    assert runs == 0


def _raw_request(inputs, **parameters):
    """A raw ``CreateExecutionRequest`` with one GitLab tool step."""

    return {
        "workflowType": "MoonMind.UserWorkflow",
        "title": "Raw GitLab run",
        "initialParameters": {
            "workflow": {
                "instructions": "Read the merge request.",
                "steps": [
                    {
                        "id": "gitlab-0",
                        "type": "tool",
                        "tool": {"id": TOOL_NAME, "inputs": inputs},
                    }
                ],
            },
            **parameters,
        },
    }


async def _counts(maker):
    from sqlalchemy import func, select

    from api_service.db.models import (
        RecurringWorkflowDefinition,
        TemporalExecutionCanonicalRecord,
    )

    async with maker() as session:
        return [
            (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            for model in (TemporalExecutionCanonicalRecord, RecurringWorkflowDefinition)
        ]


@pytest.mark.asyncio
async def test_client_authored_admissions_are_refused_on_every_create_surface(
    served, monkeypatch
):
    """Only submission writes a run's admissions; a client-supplied one is refused."""

    from unittest.mock import AsyncMock

    await _enroll_and_assign(served.client, [(PROJECT, ("read",))])
    await _seed_foreign_connection(served.maker)
    # A refusal must happen before any schedule is created.
    monkeypatch.setattr(
        "api_service.services.recurring_workflows_service.TemporalClientAdapter",
        lambda *args, **kwargs: AsyncMock(),
    )
    forged = [
        {
            "tool": TOOL_NAME,
            "connectionRef": "gitlab-foreign",
            "repository": PROJECT,
            "principalRef": "principal:foreign",
            "scopeType": "system",
            "scopeRef": None,
            "providerRepoId": "99",
            "assignmentRevision": 1,
        }
    ]
    step = _inputs(connectionRef="gitlab-foreign", read=["mr"])
    recurring = {"mode": "recurring", "cron": "0 6 * * *", "name": "Forged"}
    reads, requests = len(served.secret_reads), len(served.gitlab.requests)

    raw = await served.client.post(
        "/api/executions", json=_raw_request(step, repositoryToolAdmissions=forged)
    )
    raw_recurring = await served.client.post(
        "/api/executions",
        json={
            **_raw_request(step, repositoryToolAdmissions=forged),
            "schedule": recurring,
        },
    )
    envelope_recurring = await served.client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": _raw_request(step)["initialParameters"]["workflow"],
                "repositoryToolAdmissions": forged,
            },
            "schedule": recurring,
        },
    )

    for refused in (raw, raw_recurring, envelope_recurring):
        assert refused.status_code == 422, refused.text
        assert (
            "admissions are recorded by submission"
            in refused.json()["detail"]["message"]
        )
    assert await _counts(served.maker) == [0, 0]
    served.temporal.start_workflow.assert_not_awaited()
    assert len(served.secret_reads) == reads
    assert len(served.gitlab.requests) == requests


@pytest.mark.asyncio
async def test_raw_submission_admits_its_gitlab_steps_like_the_envelope(
    served, tmp_path
):
    from api_service.auth_providers import instance_operator_principal

    await _enroll_and_assign(served.client, [(PROJECT, ("read",))])
    await _seed_foreign_connection(served.maker)

    foreign = await served.client.post(
        "/api/executions",
        json=_raw_request(_inputs(connectionRef="gitlab-foreign", read=["mr"])),
    )
    assert foreign.status_code == 422, foreign.text
    assert "not admitted" in foreign.json()["detail"]["message"]
    assert await _counts(served.maker) == [0, 0]

    inputs = _inputs(read=["mr"])
    submitted = await served.client.post("/api/executions", json=_raw_request(inputs))
    assert submitted.status_code == 201, submitted.text
    record = await _recorded_run(served.maker, submitted.json()["workflowId"])
    assert record.parameters["repositoryToolAdmissions"] == [
        {
            "tool": TOOL_NAME,
            "connectionRef": "gitlab-main",
            "repository": PROJECT,
            "principalRef": instance_operator_principal(),
            "scopeType": "system",
            "scopeRef": None,
            "providerRepoId": "99",
            "assignmentRevision": 1,
        }
    ]
    result, records = await _execute_for(record, tmp_path, inputs)

    assert result.status == "COMPLETED", result.outputs
    assert result.outputs["mr"]["title"] == "Small slice"
    assert [r["outcome"] for r in records] == ["completed"]


@pytest.mark.asyncio
async def test_execution_scoped_principal_is_not_connection_authority(served, tmp_path):
    """A run whose admission names its own workflow principal is refused."""

    from moonmind.workflows.temporal.service import TemporalExecutionService

    await _enroll_and_assign(served.client, [(PROJECT, ("read",))])
    async with served.maker() as session:
        record = await TemporalExecutionService(
            session, client_adapter=served.temporal
        ).create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=None,
            title="Self-admitted run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"instructions": "Read the MR."}},
            idempotency_key=None,
        )
        workflow_id = record.workflow_id
    async with served.maker() as session:
        from api_service.db.models import TemporalExecutionCanonicalRecord

        canonical = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        canonical.parameters = {
            **canonical.parameters,
            "repositoryToolAdmissions": [
                {
                    "tool": TOOL_NAME,
                    "connectionRef": "gitlab-main",
                    "repository": PROJECT,
                    "principalRef": f"workflow:{workflow_id}",
                    "scopeType": "system",
                    "scopeRef": None,
                    "providerRepoId": "99",
                    "assignmentRevision": 1,
                }
            ],
        }
        await session.commit()
    reads, requests = len(served.secret_reads), len(served.gitlab.requests)

    result, _records_ = await _execute_for(
        await _recorded_run(served.maker, workflow_id),
        tmp_path,
        _inputs(read=["mr"]),
    )

    assert result.outputs["error"]["code"] == "REPOSITORY_DENIED"
    assert "not admitted" in result.outputs["error"]["message"]
    assert len(served.secret_reads) == reads
    assert len(served.gitlab.requests) == requests


@pytest.mark.asyncio
async def test_run_activity_refuses_authority_its_admission_does_not_carry(
    served, tmp_path
):
    await _enroll_and_assign(served.client, [(PROJECT, ("read",))])
    reads, requests = len(served.secret_reads), len(served.gitlab.requests)

    # Read-only assignment: the admitted note cannot be posted.
    _, (read_only, _records_) = await _run_step(
        served, tmp_path, _inputs(note={"body": "Summary"})
    )
    # Another instance than the admitted connection's.
    _, (other, _records_) = await _run_step(
        served,
        tmp_path,
        _inputs(endpoint="https://gitlab-other.example.com", read=["mr"]),
    )
    # A selection the run never admitted, though the project is assigned.
    record, _ = await _run_step(served, tmp_path, _inputs(read=["mr"]))
    unadmitted, _records_ = await _execute_for(
        record, tmp_path, _inputs(project="group/subgroup/docs", read=["mr"])
    )

    assert read_only.outputs["error"]["code"] == "BOUND_DENIED"
    assert read_only.outputs["note"]["deliveryState"] == "not_attempted"
    assert other.outputs["error"]["code"] == "gitlab_identity_rejected"
    assert unadmitted.outputs["error"]["code"] == "REPOSITORY_DENIED"
    assert "did not admit" in unadmitted.outputs["error"]["message"]
    # Only the admitted read ran; none of the refusals read the secret.
    assert len(served.secret_reads) == reads + 1
    assert len(served.gitlab.requests) == requests + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disabled", "operations", "retargeted"])
async def test_connection_changes_after_admission_refuse_the_run(
    served, tmp_path, change
):
    await _enroll_and_assign(served.client, [(PROJECT, ("read",))])
    submitted = await _submit(served.client, _inputs(read=["mr"]))
    assert submitted.status_code == 201, submitted.text
    record = await _recorded_run(served.maker, submitted.json()["workflowId"])
    client = served.client
    if change == "disabled":
        disabled = await client.post(
            "/api/v1/repository-connections/gitlab-main/disable",
            json={"requestId": "req-disable"},
        )
        assert disabled.status_code == 200, disabled.text
    elif change == "operations":
        await _assign(
            client, PROJECT, ("read", "review_request"), request_id="req-widen"
        )
    else:
        removed = await client.post(
            "/api/v1/repository-connections/gitlab-main/assignments/remove",
            json={
                "requestId": "req-remove",
                "providerRepoId": "99",
                "repository": PROJECT,
            },
        )
        assert removed.status_code == 200, removed.text
        # The path now names a different, recreated project.
        served.gitlab.projects[PROJECT] = 199
        await _assign(client, PROJECT, ("read",), request_id="req-retarget")
    reads, requests = len(served.secret_reads), len(served.gitlab.requests)

    result, records = await _execute_for(record, tmp_path, _inputs(read=["mr"]))

    assert result.status == "FAILED"
    assert result.outputs["error"]["code"] == "REPOSITORY_DENIED"
    expected = "not active" if change == "disabled" else "changed after admission"
    assert expected in result.outputs["error"]["message"]
    assert len(served.secret_reads) == reads
    assert len(served.gitlab.requests) == requests
    assert [r["outcome"] for r in records] == ["failed"]


@pytest.mark.asyncio
async def test_run_addresses_the_admitted_project_after_its_path_is_reused(
    served, tmp_path
):
    await _enroll_and_assign(served.client, [(PROJECT, ("read", "review_request"))])
    inputs = _inputs(note={"body": "Summary"})
    submitted = await _submit(served.client, inputs)
    assert submitted.status_code == 201, submitted.text
    record = await _recorded_run(served.maker, submitted.json()["workflowId"])
    # The admitted project moved away and a new project took its path.
    served.gitlab.projects[PROJECT] = 199
    requests = len(served.gitlab.requests)

    result, _records_ = await _execute_for(record, tmp_path, inputs)

    assert result.status == "COMPLETED", result.outputs
    assert result.outputs["note"]["deliveryState"] == "delivered"
    run_requests = served.gitlab.requests[requests:]
    assert {r.method for r in run_requests} == {"GET", "POST"}
    assert all(
        r.url.raw_path.startswith(b"/api/v4/projects/99/merge_requests/7")
        for r in run_requests
    )


@pytest.mark.asyncio
async def test_run_without_a_recorded_admission_gets_no_authority(served, tmp_path):
    await _enroll_and_assign(served.client, [(PROJECT, ("read",))])

    async with temporal_db(tmp_path) as session:
        result = await _dispatch(
            GitLabMergeRequestTool(),
            _inputs(read=["mr"]),
            _service(session, tmp_path),
            workflow_id="mm:unrecorded",
            principal="workflow:mm:unrecorded",
        )

    assert result.status == "FAILED"
    assert result.outputs["error"]["code"] == "REPOSITORY_CONNECTION_UNAVAILABLE"
    assert len(served.secret_reads) == 1
    assert len(served.gitlab.requests) == 1
