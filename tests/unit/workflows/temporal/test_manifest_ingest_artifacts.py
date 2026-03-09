import pytest
from temporalio import exceptions

from moonmind.workflows.temporal.workflows import manifest_ingest as manifest_module
from moonmind.workflows.temporal.workflows.manifest_ingest import (
    MoonMindManifestIngestWorkflow,
)


@pytest.mark.asyncio
async def test_manifest_ingest_workflow_returns_compiled_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindManifestIngestWorkflow()
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        calls.append((activity_type, payload))
        if activity_type == "manifest.compile":
            return {"plan_ref": "art_plan_1", "manifest_digest": "sha256:digest"}
        if activity_type == "manifest.write_summary":
            return ("art_summary_1", "art_run_index_1")
        raise AssertionError(f"Unexpected activity type: {activity_type}")

    monkeypatch.setattr(manifest_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        manifest_module.workflow,
        "info",
        lambda: type("WorkflowInfo", (), {"workflow_id": "mm:manifest-1"})(),
    )

    result = await workflow.run({"manifest_ref": "art_manifest_1", "action": "run"})

    assert result == {
        "status": "success",
        "manifest_digest": "sha256:digest",
        "plan_ref": "art_plan_1",
        "summary_ref": "art_summary_1",
    }
    assert calls[0][0] == "manifest.compile"
    assert calls[1][0] == "manifest.write_summary"
    assert "nodes" not in calls[1][1]
    assert calls[1][1]["plan_ref"] == "art_plan_1"


@pytest.mark.asyncio
async def test_manifest_ingest_workflow_requires_manifest_ref() -> None:
    workflow = MoonMindManifestIngestWorkflow()

    with pytest.raises(exceptions.ApplicationError, match="manifest_ref is required"):
        await workflow.run({})


@pytest.mark.asyncio
async def test_manifest_ingest_workflow_accepts_dict_summary_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindManifestIngestWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "manifest.compile":
            return {"plan_ref": "art_plan_2", "manifest_digest": "sha256:digest-2"}
        if activity_type == "manifest.write_summary":
            return {"summary_ref": "art_summary_2"}
        raise AssertionError(f"Unexpected activity type: {activity_type}")

    monkeypatch.setattr(manifest_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        manifest_module.workflow,
        "info",
        lambda: type("WorkflowInfo", (), {"workflow_id": "mm:manifest-2"})(),
    )

    result = await workflow.run({"manifest_ref": "art_manifest_2"})

    assert result["plan_ref"] == "art_plan_2"
    assert result["summary_ref"] == "art_summary_2"
