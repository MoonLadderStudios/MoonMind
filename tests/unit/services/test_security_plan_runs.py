"""Only a retained, digest-identified plan reaches the plan-run expansion (#5432)."""

import json
from types import SimpleNamespace

import pytest

from api_service.services import security_plan_runs
from moonmind.security.security_plan import SecurityPlanError, plan_identity
from moonmind.security.security_plan_run import PlanRunSelection
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from tests.unit.security.test_security_plan import hand_authored, validate

pytestmark = pytest.mark.asyncio


def artifacts(raw, metadata, *, denied=False):
    class Service:
        def __init__(self, _repository):
            pass

        async def get_metadata(self, *, artifact_id, access):
            if denied:
                raise TemporalArtifactAuthorizationError("denied")
            artifact = SimpleNamespace(size_bytes=len(raw), metadata_json=metadata)
            return artifact, [], None, SimpleNamespace(raw_access_allowed=True)

        async def read(self, *, artifact_id, access):
            return None, raw

    return Service


@pytest.fixture
def plan():
    return validate(hand_authored())


async def load(monkeypatch, plan, metadata, *, identity=None, denied=False, raw=None):
    raw = raw or json.dumps(plan.model_dump(mode="json", by_alias=True)).encode()
    monkeypatch.setattr(
        security_plan_runs,
        "TemporalArtifactService",
        artifacts(raw, metadata, denied=denied),
    )
    monkeypatch.setattr(
        security_plan_runs, "TemporalArtifactRepository", lambda _s: None
    )
    selection = PlanRunSelection(
        plan_ref="art_plan", identity=identity or plan_identity(plan)
    )
    return await security_plan_runs.load_retained_plan(None, selection, "access")


async def test_retained_plan_with_its_identity_loads(monkeypatch, plan):
    retained = {"producer": "security.plan", "subject": plan_identity(plan)}
    assert await load(monkeypatch, plan, retained) == plan


@pytest.mark.parametrize(
    "metadata, kwargs, message",
    [
        # A planner's unvalidated draft is not a retained plan.
        (
            {"producer": "activity:agent_runtime.publish_artifacts"},
            {},
            "not a retained security plan",
        ),
        # Another plan's identity, or edited bytes under a retained record.
        (None, {"identity": "0" * 64}, "not a retained security plan"),
        (None, {"edit": True}, "differs from the retained plan's content"),
        (None, {"denied": True}, "missing or unreadable"),
    ],
)
async def test_anything_else_is_refused_without_echoing_content(
    monkeypatch, plan, metadata, kwargs, message
):
    identity = plan_identity(plan)
    metadata = metadata or {"producer": "security.plan", "subject": identity}
    raw = None
    if kwargs.pop("edit", False):
        edited = plan.model_dump(mode="json", by_alias=True)
        edited["openPoints"][0]["text"] = "Edited after retention"
        raw = json.dumps(edited).encode()
    with pytest.raises(SecurityPlanError) as raised:
        await load(monkeypatch, plan, metadata, raw=raw, **kwargs)
    assert message in str(raised.value)
    assert "Edited after retention" not in str(raised.value)


async def test_no_operator_access_reads_nothing(plan):
    selection = PlanRunSelection(plan_ref="art_plan", identity=plan_identity(plan))
    with pytest.raises(SecurityPlanError, match="operator's artifact access"):
        await security_plan_runs.load_retained_plan(None, selection, None)
