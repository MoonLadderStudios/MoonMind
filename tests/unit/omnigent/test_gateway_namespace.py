"""Evidence links retain the actual execution namespace without broader grants."""

import dataclasses
from types import SimpleNamespace

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway


def request():
    return SimpleNamespace(step_execution=SimpleNamespace(
        workflow_id="namespace-workflow", run_id="namespace-run"))


def test_link_uses_configured_namespace_outside_activity(monkeypatch):
    monkeypatch.setattr(settings.temporal, "namespace", "configured-workers")
    link = TemporalOmnigentArtifactGateway._execution_link(
        request(), name="codex-file-seeds.json", link_type="evidence.codex_file_seeds")
    assert link.namespace == "configured-workers"
    assert (link.workflow_id, link.run_id) == ("namespace-workflow", "namespace-run")


@pytest.mark.asyncio
async def test_activity_namespace_takes_precedence_over_worker_default(monkeypatch):
    monkeypatch.setattr(settings.temporal, "namespace", "configured-workers")
    environment = ActivityEnvironment()
    environment.info = dataclasses.replace(environment.info, namespace="actual-workers")

    async def create_link():
        return TemporalOmnigentArtifactGateway._execution_link(
            request(), name="checkpoint", link_type="evidence.recovery")

    link = await environment.run(create_link)
    assert link.namespace == "actual-workers"


@pytest.mark.asyncio
async def test_verified_plan_delegation_uses_same_namespace(monkeypatch):
    monkeypatch.setattr(settings.temporal, "namespace", "configured-workers")
    calls = []

    async def link(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr('moonmind.omnigent.bridge_artifacts.link_verified_execution_plan_inputs', link)
    admitted = request()
    admitted.omnigent_execution_plan = object()
    await TemporalOmnigentArtifactGateway(None).admit_execution_plan_inputs(
        request=admitted, plan=object())
    assert calls[0]['namespace'] == 'configured-workers'
    assert calls[0]['workflow_id'] == 'namespace-workflow'
    assert calls[0]['run_id'] == 'namespace-run'
