"""The registered GitLab merge-request tool through dispatch, connection and durability.

Every test is hermetic: the GitLab instance is an ``httpx.MockTransport``, the
selected repository connection is a recorded model, the Secrets-System read is
an injected resolver behind the real bound acquisition, and results persist
through a real SQLite-backed Temporal artifact service.
"""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
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
        if request.method == "POST":
            body = json.loads(request.content)["body"]
            note = {"id": 4999 + len(self.posts), "body": body}
            # The note lands even when its acknowledgment is lost.
            self.notes.insert(0, note)
            return httpx.Response(self.post_status, json=note)
        if path == MR_PATH:
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "iid": MR_IID,
                    "project_id": 99,
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
        if path == f"{MR_PATH}/discussions":
            return httpx.Response(200, json=[{"id": "d1", "notes": []}])
        if path == f"{MR_PATH}/approvals":
            return httpx.Response(200, json={"approved": False})
        if path == f"{MR_PATH}/pipelines":
            return httpx.Response(200, json=[{"id": 5, "status": "success"}])
        if path == f"{MR_PATH}/notes":
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

    async def _load(connection_ref: str, repository: str):
        loaded.append((connection_ref, repository))
        if load is not None:
            return await load(connection_ref, repository)
        return connection

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


async def _dispatch(tool, inputs, service, *, attempt=None):
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
        "principal": "system:workflow-owner",
        "context": {
            "namespace": "default",
            "workflow_id": WORKFLOW_ID,
            "run_id": "run-1",
            "node_id": "gitlab",
        },
    }
    if attempt is None:
        return await activities._execute_skill_invocation(**invocation)
    environment = ActivityEnvironment()
    environment.info = dataclasses.replace(environment.info, attempt=attempt)
    return await environment.run(activities.mm_tool_execute, **invocation)


async def _records(service) -> list[dict]:
    access = ArtifactAccessContext.for_execution(WORKFLOW_ID)
    artifacts = await service.list_for_execution(
        namespace="default",
        workflow_id=WORKFLOW_ID,
        run_id="run-1",
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
    assert tool.loaded == [("gitlab-main", PROJECT)]
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
async def test_cross_instance_redirect_and_expired_credential_are_bounded(tmp_path):
    def _redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    redirecting = _GitLab()
    expired = _GitLab()
    expired.status_for[MR_PATH] = 401
    async with temporal_db(tmp_path) as session:
        service = _service(session, tmp_path)
        tool = GitLabMergeRequestTool(
            load_connection=lambda _ref, _repo: _async(_recorded_connection()),
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
        load_connection=lambda _ref, _repo: _async(_recorded_connection()),
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
