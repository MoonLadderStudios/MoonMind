"""#4379 replay: drift disposition distinguishes rebuilds from family changes."""

import pytest

from moonmind.omnigent.host_image_drift import describe_policy_hostclass_drift


def test_no_drift_when_images_equal():
    ref = "ghcr.io/example/host@sha256:" + "a" * 64
    assert describe_policy_hostclass_drift("p@1", ref, ref) is None


def test_same_repo_rebuild_is_adoptable_via_bootstrap():
    old = "ghcr.io/example/host@sha256:" + "a" * 64
    new = "ghcr.io/example/host@sha256:" + "b" * 64
    disposition = describe_policy_hostclass_drift("omnigent-on-demand@14", old, new)
    assert disposition is not None
    assert disposition["compatibleRebuild"] is True
    assert disposition["fencePromotion"] is True
    assert "bootstrap reconcile" in disposition["recovery"]
    assert new in disposition["recovery"]


def test_foreign_repository_fails_closed_with_explicit_revision():
    old = "ghcr.io/example/host@sha256:" + "a" * 64
    foreign = "ghcr.io/example/other@sha256:" + "b" * 64
    disposition = describe_policy_hostclass_drift("omnigent-on-demand@14", old, foreign)
    assert disposition is not None
    assert disposition["compatibleRebuild"] is False
    assert disposition["fencePromotion"] is True
    assert "explicitly" in disposition["recovery"]


def test_placeholders_never_auto_advance():
    real = "ghcr.io/example/host@sha256:" + "b" * 64
    disposition = describe_policy_hostclass_drift(
        "omnigent-on-demand@14",
        "ghcr.io/example/host@sha256:" + "0" * 64,
        real,
    )
    assert disposition is not None
    assert disposition["compatibleRebuild"] is False


@pytest.mark.asyncio
async def test_foreign_repository_compile_fails_with_actionable_evidence(
    monkeypatch,
):
    """Genuinely incompatible images still fail closed with planned/selected."""
    from types import SimpleNamespace

    import pytest

    from api_service.services import omnigent_execution_plan_service as service
    from tests.unit.omnigent.test_planning_host_drift import (
        _ArtifactService,
        _drift_policy_snapshot,
        _drift_snapshot,
        _mock_current_host,
        _PlanStore,
    )

    old_ref = "ghcr.io/example/omnigent-host@sha256:" + "a" * 64
    foreign_ref = "ghcr.io/example/other-host@sha256:" + "b" * 64

    async def resolve_policy(**_kwargs):
        return _drift_policy_snapshot(host_image_ref=old_ref)

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    _mock_current_host(monkeypatch, foreign_ref)
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: ({}, "supported"),
    )

    with pytest.raises(ValueError, match="effective launch host image conflicts") as exc:
        await service.compile_and_persist_execution_plan(
            session_factory=object(),
            artifact_service=_ArtifactService(),
            principal="user-1",
            workflow_id="mm:test-4379-foreign-evidence",
            agent_profile_snapshot=_drift_snapshot(),
            provider_profile=SimpleNamespace(
                profile_id="provider-opencode-native",
                runtime_id="opencode",
                provider_id="opencode-go",
            ),
            initial_parameters={
                "model": "example/model",
                "targetRuntime": "omnigent",
                "publishMode": "none",
                "maxAttempts": 2,
                "workflow": {"instructions": "drift."},
            },
            authored_request_ref="art_request_1",
            authored_request_digest="sha256:" + "1" * 64,
            task_input_snapshot_ref="art_request_1",
            task_input_snapshot_digest="sha256:" + "1" * 64,
            execution_plan_store=_PlanStore(object()),
        )
    message = str(exc.value)
    assert old_ref[:32] in message
    assert foreign_ref[:32] in message
