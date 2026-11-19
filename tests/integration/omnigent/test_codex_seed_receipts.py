"""Bounded frozen enrollment seeds through actual admission and lifecycle owners."""
import json

import pytest

from moonmind.omnigent.codex_file_seeds import SEED_READ_SCRIPT
from moonmind.omnigent.runtime_bindings import stable_binding_id
from tests.integration.omnigent import test_generic_native_durable_journey as native

session_factory = native.session_factory
native_journey = native.native_journey
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
TARGET = '/home/app/selected-codex-auth'
SELECTION = {'harness': 'codex-native', 'codexCredentialMount': TARGET,
    'codexEnrollmentPreferences': True,
    'additionalFiles': [{'path': TARGET + '/AGENTS.md', 'format': 'text',
        'content_template': 'append once', 'merge_strategy': 'append', 'permissions': '0600'}]}


@pytest.fixture(autouse=True)
def qualify_closed_route(monkeypatch):
    original = native.NativeDocker.command

    async def command(self, argv, **kwargs):
        if argv[:3] == ['docker', 'rm', '--force'] and argv[-1].startswith('moonmind-codex-seeds-'):
            return 0, b'', b''
        if argv == ['docker', 'volume', 'inspect', '--format', '{{.Name}}', 'journey_oauth']:
            return 0, b'journey_oauth\n', b''
        if SEED_READ_SCRIPT in argv:
            self.seed_reads = getattr(self, 'seed_reads', 0) + 1
            assert any('journey_oauth' in item and '/selected' in item and ('readonly' in item or 'read-only' in item) for item in argv)
            return 0, json.dumps({'version': 1, 'files': [
                {'path': 'AGENTS.md', 'present': True,
                 'content': getattr(self, 'seed_text', 'literal {{unbound}}')}]}).encode(), b''
        return await original(self, argv, **kwargs)
    monkeypatch.setattr(native.NativeDocker, 'command', command)


@pytest.mark.parametrize('native_journey', [SELECTION], indirect=True)
@pytest.mark.parametrize('lost_ack', [False, True])
async def test_seed_receipt_precedes_host_and_survives_lost_commit_ack(native_journey, monkeypatch, lost_ack):
    journey = native_journey
    store = journey.services.runtime_binding_store
    update = store.update
    lost = False

    async def uncertain(*args, **kwargs):
        nonlocal lost
        result = await update(*args, **kwargs)
        phases = (kwargs.get('updates') or {}).get('phaseResults', {})
        if lost_ack and not lost and any(key.startswith('codexFileSeeds:') for key in phases):
            lost = True
            raise TimeoutError('controlled lost seed receipt acknowledgement')
        return result
    monkeypatch.setattr(store, 'update', uncertain)
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    assert journey.docker.seed_reads == 1
    binding = await store.get(stable_binding_id(execution_plan_ref=journey.plan.planRef,
        idempotency_key=journey.request.idempotency_key))
    assert binding.state.value == 'cleaned'
    receipt = binding.phaseResults['codexFileSeeds:primary-model']
    assert receipt['credentialGeneration'] == 3
    assert receipt['volumeRef'] == 'journey_oauth'
    runtime = json.loads(journey.docker.launch_env['MOONMIND_CODEX_PROFILE_CONFIGURATION'])
    assert runtime['fileSeeds']['files'][0]['content'] == 'literal {{unbound}}'
    assert lost is lost_ack


@pytest.mark.parametrize('native_journey', [SELECTION], indirect=True)
async def test_ready_retry_keeps_frozen_seed_and_profile(native_journey, monkeypatch):
    import asyncio
    from api_service.db.models import ManagedAgentProviderProfile
    from moonmind.omnigent.runtime_bindings import RuntimeBindingState

    journey = native_journey
    update = journey.realizer._update_binding

    async def revoke(*args, **kwargs):
        result = await update(*args, **kwargs)
        if kwargs.get('state') is RuntimeBindingState.host_ready:
            raise asyncio.CancelledError()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(journey.realizer, '_update_binding', revoke)
        patch.setattr('moonmind.omnigent.activity_ownership.delivery_was_revoked', lambda: True)
        with pytest.raises(asyncio.CancelledError):
            await journey.realizer.execute(journey.request, journey.plan)
    assert journey.docker.seed_reads == 1
    journey.docker.seed_text = 'edited enrollment seed'
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.file_templates = []
        profile.volume_ref = 'unselected-enrollment'
        await session.commit()
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    assert journey.docker.seed_reads == 1
    configuration = json.loads(journey.docker.launch_env['MOONMIND_CODEX_PROFILE_CONFIGURATION'])
    assert configuration['fileSeeds']['files'][0]['content'] == 'literal {{unbound}}'
    assert configuration['additionalFiles'][0]['content_template'] == 'append once'


@pytest.mark.parametrize('native_journey', [SELECTION], indirect=True)
async def test_new_admission_epoch_reuses_seed_without_checkpoint(native_journey, monkeypatch):
    from moonmind.omnigent.runtime_bindings import RuntimeBindingState
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    journey = native_journey
    first_payload = journey.request.model_dump(by_alias=True, mode='json')
    first_payload['admittedProviderCapacity']['admissionEpoch'] = 1
    journey.request = AgentExecutionRequest.model_validate(first_payload)
    journey.manager.request = journey.request
    async def failed_prepare(*args, **kwargs):
        raise RuntimeError('controlled pre-host loss after seed receipt')
    with monkeypatch.context() as patch:
        patch.setattr(journey.realizer._host_runtime, 'prepare', failed_prepare)
        with pytest.raises(RuntimeError, match='controlled pre-host loss'):
            await journey.realizer.execute(journey.request, journey.plan)
    source_id = stable_binding_id(execution_plan_ref=journey.plan.planRef,
        idempotency_key=journey.request.idempotency_key, admission_epoch=1)
    source = await journey.services.runtime_binding_store.get(source_id)
    assert source.state is RuntimeBindingState.cleaned
    assert 'codexFileSeeds:primary-model' in source.phaseResults
    assert not journey.server.create_payloads
    payload = journey.request.model_dump(by_alias=True, mode='json')
    payload['admittedProviderCapacity']['admissionEpoch'] = 2
    payload['parameters']['_codexSeedAdmissionLineage'] = [{
        'executionPlanRef': journey.plan.planRef,
        'idempotencyKey': journey.request.idempotency_key,
        'admissionEpoch': 1, 'runtimeBindingRef': source_id}]
    replacement = AgentExecutionRequest.model_validate(payload)
    journey.manager.request = replacement
    journey.docker.seed_text = 'changed after original host loss'
    result = await journey.realizer.execute(replacement, journey.plan)
    assert result.failure_class is None, result
    assert journey.docker.seed_reads == 1
    runtime = json.loads(journey.docker.launch_env['MOONMIND_CODEX_PROFILE_CONFIGURATION'])
    assert runtime['fileSeeds']['files'][0]['content'] == 'literal {{unbound}}'


@pytest.mark.parametrize('native_journey', [SELECTION], indirect=True)
@pytest.mark.parametrize("fallback_writer", [False, True])
async def test_cold_checkpoint_restores_seed_after_original_workspace_and_seed_change(native_journey, monkeypatch, fallback_writer):
    import hashlib
    import shutil
    from api_service.db.models import ManagedAgentProviderProfile, ProviderProfileSlotLease
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    journey = native_journey
    cleanup_errors = []
    cleanup = journey.realizer._cleanup
    async def observe_cleanup(**kwargs):
        try:
            return await cleanup(**kwargs)
        except Exception as exc:
            cleanup_errors.append(exc)
            raise
    monkeypatch.setattr(journey.realizer, '_cleanup', observe_cleanup)
    first = await journey.realizer.execute(journey.request, journey.plan)
    assert not cleanup_errors, cleanup_errors
    assert first.failure_class is None, first
    saved = first.metadata['savedWorkspaceCheckpoint']
    from moonmind.core.artifacts import temporal_artifact_id
    checkpoint = json.loads(await journey.gateway._read_for_execution(
        temporal_artifact_id(saved['checkpointRef']), workflow_id=journey.request.step_execution.workflow_id))
    assert checkpoint['codexFileSeedReceipts']['primary-model']['volumeRef'] == 'journey_oauth'
    if fallback_writer:
        import dataclasses
        from temporalio.testing import ActivityEnvironment
        from moonmind.workflows.temporal.activity_runtime import TemporalCheckpointActivities
        from moonmind.workflows.temporal.artifacts import TemporalArtifactRepository, TemporalArtifactService
        environment = ActivityEnvironment()
        environment.info = dataclasses.replace(environment.info, namespace='default',
            workflow_id=journey.request.step_execution.workflow_id,
            workflow_run_id=journey.request.step_execution.run_id,
            activity_type='step_checkpoint.create_v2')
        payload = {key: checkpoint[key] for key in (
            'boundary', 'taskInputSnapshotRef', 'workspace', 'createdAt',
            'planRef', 'planDigest', 'codexFileSeedReceipts')}
        payload.update(identity=checkpoint['source'],
            codexFileSeedSource=first.metadata['codexFileSeedSource'],
            idempotencyKey='fallback-cold-seed')
        async with journey.session_factory() as session:
            writer = TemporalCheckpointActivities(artifact_service=TemporalArtifactService(TemporalArtifactRepository(session)))
            fallback = await environment.run(writer.step_checkpoint_create, payload)
        saved = {**saved, 'checkpointRef': fallback['checkpointRef']}
    source_binding = await journey.services.runtime_binding_store.get(checkpoint['codexFileSeedSource']['runtimeBindingRef'])
    assert source_binding is not None
    assert any(temporal_artifact_id(value.get('checkpointRef')) == temporal_artifact_id(saved['checkpointRef'])
        for key, value in source_binding.phaseResults.items()
        if key == 'saved' or key.startswith('codexSeedCheckpoint:'))
    shutil.rmtree(journey.server.workspace)
    identity = journey.request.step_execution
    step_id = f'{identity.workflow_id}:{identity.run_id}:{identity.logical_step_id}:execution:2'
    payload = journey.request.model_dump(by_alias=True, mode='json', exclude_none=True)
    payload['idempotencyKey'] = 'frozen-seed-successor'
    payload['stepExecution'].update(executionOrdinal=2, stepExecutionId=step_id)
    payload['workspaceSpec'] = {'workspaceCheckpointRestoreRef': saved['archiveRef'],
        'workspaceLocator': {'kind': 'sandbox', 'workspaceId': hashlib.sha256(
            f'{identity.workflow_id}:{step_id}'.encode()).hexdigest()[:24], 'relativePath': 'repo'}}
    payload['admittedProviderCapacity'].update(stepExecutionId=step_id,
        idempotencyKey='frozen-seed-successor', agentRunRunId='seed-successor-run')
    metadata = payload['parameters'].setdefault('metadata', {})
    metadata.setdefault('moonmind', {}).update(sharedStepCheckpointRef=saved['checkpointRef'],
        sharedCheckpointRequired=True)
    successor = AgentExecutionRequest.model_validate(payload)
    journey.manager.request = successor
    async with journey.session_factory() as session:
        lease = await session.get(ProviderProfileSlotLease, journey.provider_lease_id)
        lease.step_execution_id = step_id
        lease.idempotency_key = successor.idempotency_key
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.file_templates = []
        profile.volume_ref = 'different-live-enrollment'
        await session.commit()
    await journey.gateway.admit_execution_plan_inputs(request=successor, plan=journey.plan)
    journey.docker.seed_text = 'never recapture this new seed'
    journey.server.terminal_status, journey.server.status = 'idle', 'idle'
    journey.server.items, journey.server.events = [], []
    result = await journey.realizer.execute(successor, journey.plan)
    assert result.failure_class is None, result
    assert journey.docker.seed_reads == 1
    runtime = json.loads(journey.docker.launch_env['MOONMIND_CODEX_PROFILE_CONFIGURATION'])
    assert runtime['fileSeeds']['files'][0]['content'] == 'literal {{unbound}}'
    assert runtime['additionalFiles'][0]['content_template'] == 'append once'


@pytest.mark.parametrize('native_journey', [SELECTION], indirect=True)
async def test_unsafe_seed_refuses_before_host_or_session(native_journey):
    from moonmind.omnigent.codex_file_seeds import CodexFileSeedUnavailable
    journey = native_journey
    journey.docker.seed_text = 'api_key=synthetic-secret'
    with pytest.raises(CodexFileSeedUnavailable, match='unsafe'):
        await journey.realizer.execute(journey.request, journey.plan)
    assert journey.docker.create_count == 0
    assert not journey.server.create_payloads
    assert 'journey_oauth' in journey.docker.volumes


@pytest.mark.parametrize('native_journey', [SELECTION], indirect=True)
async def test_queued_intent_uses_first_materialization_seed_not_edited_profile(native_journey):
    from api_service.db.models import ManagedAgentProviderProfile
    journey = native_journey
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.file_templates = []
        profile.volume_ref = 'unselected-later-profile-volume'
        await session.commit()
    journey.docker.seed_text = 'seed present when native materialization begins'
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    runtime = json.loads(journey.docker.launch_env['MOONMIND_CODEX_PROFILE_CONFIGURATION'])
    assert runtime['fileSeedPolicy'] == 'capture-once-v1'
    assert runtime['fileSeeds']['files'][0]['content'] == journey.docker.seed_text
    assert runtime['additionalFiles'][0]['content_template'] == 'append once'
    assert journey.docker.seed_reads == 1


@pytest.mark.parametrize('native_journey', [SELECTION], indirect=True)
async def test_fallback_checkpoint_writer_verifies_durable_seed_owner(native_journey, monkeypatch):
    from copy import deepcopy
    from moonmind.core.artifacts import temporal_artifact_id
    from moonmind.workflows.temporal.activity_runtime import TemporalCheckpointActivities
    from moonmind.workflows.temporal.artifacts import TemporalArtifactRepository, TemporalArtifactService

    journey = native_journey
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    saved = result.metadata['savedWorkspaceCheckpoint']
    checkpoint = json.loads(await journey.gateway._read_for_execution(
        temporal_artifact_id(saved['checkpointRef']),
        workflow_id=journey.request.step_execution.workflow_id))
    payload = {key: checkpoint[key] for key in (
        'boundary', 'taskInputSnapshotRef', 'workspace', 'createdAt',
        'planRef', 'planDigest', 'codexFileSeedReceipts')}
    payload['identity'] = checkpoint['source']
    payload['codexFileSeedSource'] = result.metadata['codexFileSeedSource']
    payload['idempotencyKey'] = 'fallback-seed-writer'
    import dataclasses
    from temporalio.testing import ActivityEnvironment
    environment = ActivityEnvironment()
    environment.info = dataclasses.replace(environment.info, namespace='default',
        workflow_id=journey.request.step_execution.workflow_id,
        workflow_run_id=journey.request.step_execution.run_id,
        activity_type='step_checkpoint.create_v2')
    async with journey.session_factory() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        writer = TemporalCheckpointActivities(artifact_service=service)
        written = await environment.run(writer.step_checkpoint_create, payload)
        assert written['checkpointRef']
        repeated = await environment.run(writer.step_checkpoint_create, payload)
        assert repeated['checkpointRef'] == written['checkpointRef']
        owner = await journey.services.runtime_binding_store.get(payload['codexFileSeedSource']['runtimeBindingRef'])
        assert any(value.get('checkpointRef') == written['checkpointRef']
                   for key, value in owner.phaseResults.items() if key.startswith('codexSeedCheckpoint:'))
        forged = deepcopy(payload)
        forged['codexFileSeedReceipts']['primary-model']['volumeRef'] = 'sibling-volume'
        with pytest.raises(RuntimeError, match='durable source evidence'):
            await environment.run(writer.step_checkpoint_create, forged)
