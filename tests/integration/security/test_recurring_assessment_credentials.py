"""Recurring credential changes through durable job and secret owners."""

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from temporalio.testing import ActivityEnvironment

from tests.integration.security import test_recurring_assessments as recurring

environment = recurring.environment
temporal_environment = recurring.temporal_environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def save_authorization(sessions, record):
    """Exercise Settings' generation and optimistic concurrency owner."""
    from sqlalchemy import select

    from api_service.db.models import SettingsOverride
    from api_service.services.assessment_authorizations import (
        load_assessment_authorization,
    )
    from api_service.services.settings_catalog import SettingsCatalogService
    from moonmind.security.assessment_authorizations import SETTINGS_KEY

    async with sessions() as session:
        row = await session.scalar(
            select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
        )
        await SettingsCatalogService(session=session, env={}).apply_overrides(
            changes={
                SETTINGS_KEY: {
                    "authorizations": [record.model_dump(mode="json", by_alias=True)]
                }
            },
            expected_versions={SETTINGS_KEY: row.value_version if row else 1},
        )
        return await load_assessment_authorization(session, record.ref)


@pytest.mark.parametrize("change", ["grant_revoked", "secret_rotated"])
async def test_recurring_credential_changes_recheck_occurrences_and_job_access(
    environment, temporal_environment, tmp_path, monkeypatch, change
):
    """Use real reservations, Temporal inputs, secret storage and worker guards.

    Only the Docker daemon and cloud issuer are provider doubles. In particular,
    the worker's identity and credential decisions come from the job started by
    ContainerJobService, rather than a separately authored worker request.
    """
    from api_service.db.models import AssessmentOccurrenceRecord, ContainerJobRecord
    from api_service.services import assessment_authorizations as authority
    from api_service.services import container_jobs
    from api_service.services.secrets import SecretsService
    from moonmind.schemas.container_job_models import (
        ContainerJobActivityRequest,
        ContainerJobBackendError,
        ContainerJobFailureClass,
        ContainerJobSubmitRequest,
        ContainerJobWorkflowInput,
        container_job_workflow_id,
    )
    from moonmind.security import workload_credentials
    from moonmind.security.assessment_authorizations import authorization_scope
    from moonmind.workflows.temporal import container_job_backend, worker_runtime
    from moonmind.workflows.temporal.client import TemporalClientAdapter
    from moonmind.workflows.temporal.container_job_attachments import storage_for
    from tests.unit.services.test_assessment_job_authority import (
        authorization,
        capability,
        save_scope,
    )
    from tests.unit.workflows.temporal import (
        test_container_job_workload_credentials as cloud,
    )

    moment = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    for module in (
        authority,
        container_jobs,
        container_job_backend,
        workload_credentials,
    ):
        monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(
        worker_runtime, "get_async_session_context", environment.sessions
    )
    async with environment.sessions() as session:
        secret = await SecretsService.create_secret(
            session, "aws-audit", json.dumps(cloud.SOURCE)
        )
        await SecretsService.create_secret(session, "app-login", cloud.APP_VALUE)
        credential_revision = secret.credential_revision
    admitted = cloud.scope().model_dump(mode="json", by_alias=True)
    admitted["grants"][0]["revision"] = credential_revision
    admitted.update(maxCpuMillis=2000, maxMemoryMiB=2048)
    record = authorization(
        scope=admitted,
        validFrom=moment - timedelta(hours=1),
        validUntil=moment + timedelta(hours=3),
        maxTotalJobSeconds=7200,
        recurring={
            "minIntervalSeconds": 60,
            "maxOccurrences": 3,
            "maxJobsPerOccurrence": 1,
            "maxJobSecondsPerOccurrence": 1800,
            "maxRunSeconds": 2400,
        },
    )
    record = await save_authorization(environment.sessions, record)
    scope = authorization_scope(record)
    provider = cloud.Fixture(tmp_path)
    backend = container_job_backend.DockerContainerJobBackend(
        workspace_root=provider.root,
        settings=cloud.SETTINGS,
        command_runner=provider.daemon,
        evidence_publisher=provider.publish,
        secret_resolver=worker_runtime._container_job_secret_resolver(),
        cloud_session_issuer=provider.sts,
        assessment_authority=worker_runtime._container_job_assessment_authority(
            cloud.SETTINGS
        ),
        log_spool_root=tmp_path / "live-recurring",
    )
    adapter = TemporalClientAdapter(client=temporal_environment.client)
    handles = []

    async def reserve_and_submit(workflow_id, current_scope):
        async with environment.sessions() as session:
            await save_scope(session, current_scope, workflow=workflow_id)
            occurrence = await authority.admit_assessment_occurrence(
                session, workflow_id=workflow_id
            )
            await session.commit()
            cap = capability(current_scope, owner=uuid4().hex, workflow=workflow_id)
            accepted = await container_jobs.ContainerJobService(
                session,
                temporal=adapter,
                backend_settings=cloud.SETTINGS,
                capability=cap,
            ).submit(
                owner=cap.owner,
                request=ContainerJobSubmitRequest(
                    idempotencyKey=uuid4().hex,
                    source={"source": "workflow", "workflowId": workflow_id},
                    spec=provider.request.request.spec,
                ),
            )
        handle = temporal_environment.client.get_workflow_handle(
            container_job_workflow_id(accepted.job_id)
        )
        handles.append(handle)
        start = ContainerJobWorkflowInput.model_validate(
            await adapter.read_workflow_start_input(
                handle.id, run_id=(await handle.describe()).run_id
            )
        )
        request = ContainerJobActivityRequest(
            jobId=start.job_id,
            owner=start.owner,
            ownershipToken=start.ownership_token,
            request=start.request,
            workloadCredentials=start.workload_credentials,
            resolvedImageRef=cloud.IMAGE,
            resolvedWorkspaceRef=provider.request.resolved_workspace_ref,
        )
        return occurrence, request

    async def create(request):
        handle = temporal_environment.client.get_workflow_handle(
            container_job_workflow_id(request.job_id)
        )
        activity_env = ActivityEnvironment()
        activity_env.info = replace(
            activity_env.info,
            namespace=temporal_environment.client.namespace,
            workflow_id=container_job_workflow_id(request.job_id),
            workflow_run_id=(await handle.describe()).run_id,
        )
        return await activity_env.run(backend.create_container, request)

    async def retained(workflow_id, request):
        async with environment.sessions() as session:
            occurrence = await session.get(AssessmentOccurrenceRecord, workflow_id)
            job = await session.get(ContainerJobRecord, request.job_id)
            return copy.deepcopy(occurrence.authority_json), job.assessment_deadline_at

    first_id, next_id = "assessment-" + uuid4().hex, "assessment-" + uuid4().hex
    try:
        first, first_request = await reserve_and_submit(first_id, scope)
        await create(first_request)
        original = await retained(first_id, first_request)
        lease = json.loads(
            storage_for(provider.root, first_request.job_id)
            .workload_credentials.joinpath("lease.json")
            .read_text()
        )
        assert len(provider.sts.calls) == 1
        assert provider.sts.calls[0]["duration_seconds"] == 1800
        assert datetime.fromisoformat(lease["budgetEndsAt"]) == moment + timedelta(
            seconds=1800
        )
        assert datetime.fromisoformat(lease["budgetEndsAt"]) <= datetime.fromisoformat(
            first["expiresAt"]
        )
        assert (
            first_request.workload_credentials[0].credential_revision
            == credential_revision
        )
        assert first["authorizationGeneration"] == record.generation
        moment += timedelta(seconds=60)
        async with environment.sessions() as session:
            if change == "grant_revoked":
                changed = record.model_dump(mode="json", by_alias=True)
                changed["scope"]["grants"] = []
                await save_authorization(
                    environment.sessions, type(record).model_validate(changed)
                )
            else:
                rotated = await SecretsService.rotate_secret(
                    session,
                    "aws-audit",
                    json.dumps(
                        {**cloud.SOURCE, "secretAccessKey": "rotated-fixture-value"}
                    ),
                    expected_credential_revision=credential_revision,
                    validator=lambda candidate: json.loads(candidate)["accessKeyId"]
                    == cloud.SOURCE["accessKeyId"],
                )
                assert rotated.credential_revision == credential_revision + 1
                assert rotated.policy_revision == secret.policy_revision == 1
        commands_before = len(provider.commands)
        if change == "grant_revoked":
            async with environment.sessions() as session:
                await save_scope(session, scope, workflow=next_id)
                with pytest.raises(
                    authority.AssessmentAuthorizationDenied, match="changed|revoked"
                ):
                    await authority.admit_assessment_occurrence(
                        session, workflow_id=next_id
                    )
                cap = capability(scope, workflow=next_id)
                with pytest.raises(
                    container_jobs.ContainerJobScopeDeniedError, match="changed|revoked"
                ):
                    await container_jobs.ContainerJobService(
                        session,
                        temporal=adapter,
                        backend_settings=cloud.SETTINGS,
                        capability=cap,
                    ).submit(
                        owner=cap.owner,
                        request=ContainerJobSubmitRequest(
                            idempotencyKey=uuid4().hex,
                            source={"source": "workflow", "workflowId": next_id},
                            spec=provider.request.request.spec,
                        ),
                    )
                assert await session.get(AssessmentOccurrenceRecord, next_id) is None
            failure = ContainerJobFailureClass.ASSESSMENT_AUTHORITY_DENIED
        else:
            second, second_request = await reserve_and_submit(next_id, scope)
            assert second["ordinal"] == 2
            assert second["expiresAt"] == (moment + timedelta(seconds=2400)).isoformat()
            assert (
                second_request.workload_credentials[0].credential_revision
                == credential_revision
            )
            assert second_request.job_id != first_request.job_id
            second_original = await retained(next_id, second_request)
            failure = ContainerJobFailureClass.CREDENTIAL_UNRESOLVED
            with pytest.raises(ContainerJobBackendError) as denied:
                await create(second_request)
            assert denied.value.failure_class is failure
            assert await retained(next_id, second_request) == second_original
        # A delayed/restarted action of the first occurrence also rechecks access.
        with pytest.raises(ContainerJobBackendError) as denied:
            await create(first_request)
        assert denied.value.failure_class is failure
        assert len(provider.commands) == commands_before
        assert len(provider.sts.calls) == 1
        assert await retained(first_id, first_request) == original
        assert (
            json.loads(
                storage_for(provider.root, first_request.job_id)
                .workload_credentials.joinpath("lease.json")
                .read_text()
            )["budgetEndsAt"]
            == lease["budgetEndsAt"]
        )

        # Explicit review can restore access, but cannot refund either reservation
        # or reinterpret a secret revision as a policy/authorization generation.
        reviewed = record.model_dump(mode="json", by_alias=True)
        reviewed["scope"]["grants"][0]["revision"] = credential_revision + (
            change == "secret_rotated"
        )
        renewed = await save_authorization(
            environment.sessions, type(record).model_validate(reviewed)
        )
        moment += timedelta(seconds=60)
        final_id = "assessment-" + uuid4().hex
        final, final_request = await reserve_and_submit(
            final_id, authorization_scope(renewed)
        )
        await create(final_request)
        assert final["ordinal"] == (3 if change == "secret_rotated" else 2)
        assert (
            final["authorizationGeneration"] == renewed.generation != record.generation
        )
        assert (
            final_request.workload_credentials[0].credential_revision
            == reviewed["scope"]["grants"][0]["revision"]
        )
        assert len(provider.sts.calls) == 2
        assert all(call["duration_seconds"] == 1800 for call in provider.sts.calls)
        final_lease = json.loads(
            storage_for(provider.root, final_request.job_id)
            .workload_credentials.joinpath("lease.json")
            .read_text()
        )
        assert datetime.fromisoformat(
            final_lease["budgetEndsAt"]
        ) == moment + timedelta(seconds=1800)
        assert final_lease["budgetEndsAt"] != lease["budgetEndsAt"]
        assert await retained(first_id, first_request) == original
        if change == "secret_rotated":
            assert await retained(next_id, second_request) == second_original
    finally:
        for handle in handles:
            await handle.terminate("Recurring credential fixture cleanup")
