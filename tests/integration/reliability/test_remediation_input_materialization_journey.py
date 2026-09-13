"""Replay scheduled remediation evidence through the real artifact/host boundary."""
from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from moonmind.omnigent.execute import _build_omnigent_first_message, _first_message_text
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore, TemporalArtifactRepository, TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize('existing_workspace', [False, True])
@pytest.mark.parametrize('separate_remaining_work', [False, True])
async def test_recurring_gate_refs_reach_readable_first_message_paths(
    tmp_path, existing_workspace, separate_remaining_work,
):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/artifacts.db')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / 'artifacts'),
            )

            async def publish(payload, owner='workflow-1'):
                artifact, _ = await service.create(
                    principal='default-user', content_type='application/json',
                    link={'namespace': 'moonmind', 'workflow_id': owner,
                          'run_id': 'run-1', 'link_type': 'output.primary'},
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id, principal='default-user',
                    payload=json.dumps(payload).encode(), content_type='application/json',
                )
                return f'artifact://{artifact.artifact_id}'

            brief = await publish({'issue': 4193})
            gate_payload = {'verdict': 'ADDITIONAL_WORK_NEEDED', 'remainingWork': ['fix']}
            gate = await publish(gate_payload)
            remaining = await publish(['fix']) if separate_remaining_work else gate
            workspace_id = hashlib.sha256(b'workflow-1:step-1').hexdigest()[:24]
            workspace = tmp_path / 'workspaces' / 'temporal_sandbox' / workspace_id / 'repo'
            (workspace / '.git' / 'info').mkdir(parents=True)
            (workspace / 'candidate.txt').write_text('preserved implementation')
            fixture = json.loads((Path(__file__).with_name('replays') / 'recurring-remediation-evidence' / 'request.json').read_text())
            replay = dict(fixture['request'])
            replay['inputRefs'] = [brief]
            replay['parameters'] = {'gateResultRef': gate, 'remainingWorkRef': remaining}
            request = AgentExecutionRequest(
                **replay,
                instructionRef='Execute the resolved Skill using the verifier evidence.',
                workspaceSpec={
                    'workspaceLocator': {'kind': 'sandbox', 'workspaceId': workspace_id,
                                         'relativePath': 'repo'},
                    'repository': 'MoonLadderStudios/MoonMind', 'branch': 'main',
                },
            )

            async def no_clone(*args, **kwargs):
                raise AssertionError('candidate must remain authoritative')

            materializer = OmnigentWorkspaceMaterializer(
                command_runner=no_clone, workspace_root=tmp_path / 'workspaces',
                artifact_service=service,
            )
            identity = {'runtime_uid': os.getuid(), 'runtime_gid': os.getgid()}
            if existing_workspace:
                await materializer.materialize(request.model_copy(update={'parameters': {}}), **identity)
            attachment = await materializer.materialize(request, **identity)
            # Actual generic-host binding must carry the host-owned paths into
            # the message builder. Provider session/model selection is unchanged.
            plan = SimpleNamespace(planRef='plan-1', payload=SimpleNamespace(
                agentSource={'upstreamId': 'agent-1'}, endpointRef='endpoint-1',
                harnessId='any-qualified-harness', hostClassRef='host-1',
                launchPolicyRef='launch-1', capturePolicy={},
                modelConfig=SimpleNamespace(qualifiedId='same-model', effort='xhigh'),
            ))
            binding = SimpleNamespace(bindingId='binding-1', providerLeases={},
                                      hostBindingRef='binding-host', hostLeaseRef='lease-1')
            realizer = object.__new__(GenericOmnigentHostRealizer)
            bound = realizer._bind_exact_host(request, plan, {
                'omnigentHostId': 'host-1', 'workspacePath': '/workspaces/run',
                'materializedInputPaths': attachment['materializedInputPaths'],
            }, binding)
            message = await _build_omnigent_first_message(request=bound, prompt={}, artifact_gateway=None)
            text = _first_message_text(message)
            paths = attachment['materializedInputPaths']
            for name, path in paths.items():
                assert f'- {name}: {path}' in text
                assert (workspace / path).is_file()
            assert json.loads((workspace / paths['gateResultPath']).read_text()) == gate_payload
            assert json.loads((workspace / paths['remainingWorkPath']).read_text()) == (['fix'] if separate_remaining_work else gate_payload)
            assert (workspace / 'candidate.txt').read_text() == 'preserved implementation'
            assert bound.parameters['omnigent']['session']['modelOverride'] == 'same-model'
            # Replayed requests re-admit the same inputs without truncating
            # read-only files, restoring a checkpoint, or replacing the candidate.
            assert (await materializer.materialize(request, **identity))['materializedInputPaths'] == paths
            foreign = await publish({'secret': 'foreign'}, owner='other-workflow')
            rejected = request.model_copy(update={'parameters': {'gateResultRef': foreign}})
            with pytest.raises(HarnessPlatformError, match='workflow'):
                await materializer.materialize(rejected, **identity)
            assert (workspace / 'candidate.txt').read_text() == 'preserved implementation'
    finally:
        await engine.dispose()
