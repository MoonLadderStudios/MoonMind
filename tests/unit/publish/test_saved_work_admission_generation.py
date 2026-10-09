"""An unchanged saved publication can be explicitly re-admitted, not rewritten."""

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from temporalio.exceptions import ApplicationError

from api_service.api.routers import executions
from api_service.services.repository_connections import RepositoryConnectionService
from moonmind.workflows.executions.repository_contract import DEFAULT_GIT_CONNECTION_REF
from moonmind.workflows.temporal.artifacts import find_saved_work_publication_decisions
from moonmind.workflows.temporal.client import WorkflowStartResult
from moonmind.workflows.temporal.publication_recovery import (
    SavedWorkPublicationContract,
    SavedWorkPublicationDestination,
    saved_work_publication_operation_key,
)
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
)
from tests.unit.api.routers.test_executions import (
    _SAVED_WORK_BODY,
    _saved_work_app,
    _SavedWorkArtifacts,
)
from tests.unit.publish.test_saved_work_destination_authority import (
    recorded_destination,
)
from tests.unit.publish.test_saved_work_publication_journey import (
    OPERATOR,
    REPOSITORY,
    _WorkerLost,
)


def test_omitted_generation_keeps_the_literal_prechange_operation_key():
    destination = SavedWorkPublicationDestination.model_validate(
        _SAVED_WORK_BODY["destination"]
    )
    assert (
        saved_work_publication_operation_key(
            saved_work_digest="sha256:" + "a" * 64,
            destination=destination,
            github_authority_ref="github:repository-default",
        )
        == "saved-publication:c418115c22e085c9a3cf35d4cbcfa12dca4318a11ee5a5a7ea1a48bf5b70c339"
    )


def test_public_api_freezes_generation_and_retains_omitted_legacy_identity(monkeypatch):
    app, adapter, _record, _user = _saved_work_app(monkeypatch, _SavedWorkArtifacts())
    adapter.start_workflow.side_effect = lambda **kwargs: WorkflowStartResult(
        workflow_id=kwargs["workflow_id"], run_id="publication-run"
    )
    with TestClient(app) as client:
        legacy = client.post(
            "/api/executions/mm:wf-1/retry-publication", json=_SAVED_WORK_BODY
        )
        for generation in ("admission-a", "admission-a", "admission-b"):
            response = client.post(
                "/api/executions/mm:wf-1/retry-publication",
                json={**_SAVED_WORK_BODY, "admissionGeneration": generation},
            )
            assert response.status_code == 201, response.json()
    calls = [call.kwargs for call in adapter.start_workflow.await_args_list]
    assert legacy.status_code == 201
    old, first, retry, fresh = calls
    destination = SavedWorkPublicationDestination.model_validate(
        _SAVED_WORK_BODY["destination"]
    )
    assert old["input_args"][
        "publicationIdempotencyKey"
    ] == saved_work_publication_operation_key(
        saved_work_digest=old["input_args"]["savedWorkDigest"],
        destination=destination,
        github_authority_ref="github:repository-default",
    )
    assert first == retry
    assert first["workflow_id"] != fresh["workflow_id"] != old["workflow_id"]
    assert first["input_args"]["admissionGeneration"] == "admission-a"
    assert fresh["input_args"]["admissionGeneration"] == "admission-b"
    for field in (
        "destination",
        "commit",
        "savedWorkDigest",
        "pullRequestTitle",
        "pullRequestBody",
    ):
        assert (
            old["input_args"][field]
            == first["input_args"][field]
            == fresh["input_args"][field]
        )
    retained = SavedWorkPublicationContract.model_validate(first["input_args"])
    assert (
        SavedWorkPublicationContract.model_validate(retained.model_dump(by_alias=True))
        == retained
    )
    with pytest.raises(ValueError, match="PUBLICATION_IDEMPOTENCY_MISMATCH"):
        SavedWorkPublicationContract.model_validate(
            {**first["input_args"], "admissionGeneration": "other"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["credential", "policy"])
async def test_normal_api_fresh_generation_after_fenced_prepare_preserves_output_and_receipts(
    tmp_path, monkeypatch, change
):
    assignment = github_repository_assignment(DEFAULT_GIT_CONNECTION_REF, REPOSITORY)
    async with recorded_destination(tmp_path, monkeypatch, assignment=assignment) as (
        state,
        engine,
        connection,
    ):
        app, adapter, record, user = _saved_work_app(monkeypatch, state.service)
        record.workflow_id = state.saved.workflow_id
        record.run_id = state.saved.run_id
        record.search_attributes["mm_owner_id"] = str(user.id)
        # Real artifact reads use the same operator that captured the saved work.
        monkeypatch.setattr(executions, "_execution_principal", lambda _user: OPERATOR)
        admitted = []
        adapter.start_workflow.side_effect = lambda **kwargs: (
            admitted.append(kwargs)
            or WorkflowStartResult(
                workflow_id=kwargs["workflow_id"], run_id="publication-run"
            )
        )
        body = {
            "savedWorkRef": state.saved.saved_work_ref,
            "sourceRunId": state.saved.run_id,
            "destination": {
                "repository": REPOSITORY,
                "objective": "pr",
                "baseBranch": "main",
                "headBranch": "saved/work",
                "strategy": "additive_import",
            },
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:

            async def submit(generation):
                response = await client.post(
                    f"/api/executions/{state.saved.workflow_id}/retry-publication",
                    json={**body, "admissionGeneration": generation},
                )
                assert response.status_code == 201, response.json()
                return admitted[-1]["input_args"]

            old = await submit("admission-a")
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            captured = {}

            async def lose_prepare_ack(attempt):
                if attempt != 1:
                    return
                captured["objects"] = state.saved_bytes()
                captured["commands"] = list(state.git_commands)
                captured["decisions"] = await find_saved_work_publication_decisions(
                    state.service,
                    workflow_id="mm:source:saved-work-publication:x",
                    operation_key=old["publicationIdempotencyKey"],
                )
                assert len(captured["decisions"]) == 1
                updated = connection.model_copy(update={"display_name": "New policy"})
                if change == "credential":
                    updated = github_pat_connection(
                        DEFAULT_GIT_CONNECTION_REF, "SAVED_DESTINATION_PAT_B"
                    ).model_copy(update={"credential_revision": 2})
                async with sessions() as session:
                    await RepositoryConnectionService(session).update_connection(
                        updated,
                        actor_ref="system:deployment",
                        request_id="fresh-admission-change",
                        expected_policy_revision=1,
                        principal_ref="system:deployment",
                        principal_scope=("system", None),
                    )
                raise _WorkerLost()

            state.after["publication_recovery.saved_work_prepare"] = lose_prepare_ack
            with pytest.raises(ApplicationError) as exc:
                await state.run(old)
            assert exc.value.type == "PUBLICATION_AUTHORITY_CHANGED"
            retry = await submit("admission-a")
            assert retry == old
            with pytest.raises(ApplicationError) as exc:
                await state.run(retry)
            assert exc.value.type == "PUBLICATION_AUTHORITY_CHANGED"
            assert state.git_commands == captured["commands"]
            assert state.pushes() == [] and state.provider.creates == []
            assert state.saved_objects_unchanged(captured["objects"])

            fresh = await submit("admission-b")
            assert fresh["destination"] == old["destination"]
            assert fresh["commit"] == old["commit"]
            assert fresh["pullRequestTitle"] == old["pullRequestTitle"]
            assert fresh["pullRequestBody"] == old["pullRequestBody"]
            assert (
                fresh["publicationIdempotencyKey"] != old["publicationIdempotencyKey"]
            )
            state.after.clear()
            result = await state.run(fresh)
            assert result["outcome"] == "published"
            assert result["implementationRerun"] is False
            assert result["verificationRerun"] is False
            assert result["push"]["remoteVerified"] is True
            assert result["candidate"] == captured["decisions"][0]["candidate"]
            assert len(state.pushes()) == 1 and len(state.provider.creates) == 1
            assert state.saved_objects_unchanged(captured["objects"])
            # A restart of the admitted new generation reconciles its exact receipts.
            restarted = SavedWorkPublicationContract.model_validate(fresh).model_dump(
                by_alias=True, mode="json"
            )
            again = await state.run(restarted)
            assert again["outcome"] == "published"
            assert again["candidate"] == result["candidate"]
            assert len(state.pushes()) == 1 and len(state.provider.creates) == 1
            old_decisions = await find_saved_work_publication_decisions(
                state.service,
                workflow_id="mm:source:saved-work-publication:x",
                operation_key=old["publicationIdempotencyKey"],
            )
            assert old_decisions == captured["decisions"]
            fresh_decisions = await find_saved_work_publication_decisions(
                state.service,
                workflow_id="mm:source:saved-work-publication:x",
                operation_key=fresh["publicationIdempotencyKey"],
            )
            assert len(fresh_decisions) == 2  # new run keeps its own reuse receipt
            assert len({item["decisionDigest"] for item in fresh_decisions}) == 1
