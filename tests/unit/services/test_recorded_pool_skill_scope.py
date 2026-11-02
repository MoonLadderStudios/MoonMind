"""A recorded Skill manifest cannot confer another execution's artifacts."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services import omnigent_execution_plan_service as service
from moonmind.workflows.temporal.artifacts import ArtifactAccessContext


@pytest.mark.asyncio
async def test_recorded_pool_skill_content_keeps_execution_access_boundary(monkeypatch):
    access = ArtifactAccessContext.for_execution("accepted-parent")
    expected_access = access
    manifest = {
        "snapshot_id": "accepted-skills",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "skills": [
            {
                "skill_name": "accepted-skill",
                "content_ref": "artifact:sibling-content",
                "content_digest": "sha256:" + "a" * 64,
                "provenance": {"source_kind": "deployment"},
            }
        ],
    }
    body = json.dumps(manifest).encode()

    async def read(*, artifact_id, access):
        assert access is expected_access
        if artifact_id == "accepted-manifest":
            return SimpleNamespace(sha256=sha256(body).hexdigest()), body
        raise PermissionError("content belongs to another execution")

    artifacts = SimpleNamespace(
        read=AsyncMock(side_effect=read), record_verified_dependencies=AsyncMock()
    )
    persist = AsyncMock(return_value=("canonical-copy", "sha256:" + "b" * 64))
    monkeypatch.setattr(service, "persist_json_artifact", persist)
    with pytest.raises(PermissionError, match="another execution"):
        await service._resolve_and_persist_skills(
            session_factory=None,
            artifact_service=artifacts,
            principal="operator",
            workflow_id="accepted-parent",
            task_input_snapshot_digest="sha256:" + "c" * 64,
            initial_parameters={},
            access=access,
            recorded_skillset_ref="artifact:accepted-manifest",
        )
    artifacts.record_verified_dependencies.assert_not_awaited()
    persist.assert_not_awaited()
