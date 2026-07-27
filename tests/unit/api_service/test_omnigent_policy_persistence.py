"""Database-backed lifecycle evidence for persistent Omnigent policy authority."""

from copy import deepcopy

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentPolicy, OmnigentPolicyVersion
from api_service.services.omnigent_policies import (
    OmnigentPolicyService,
    PolicyConflict,
    bootstrap_document,
    seed_bootstrap_policies,
)
from moonmind.omnigent.policies import PolicyDocument, PolicyState


@pytest_asyncio.fixture
async def policy_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/policies.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _document() -> PolicyDocument:
    return bootstrap_document(
        host_mode="static_compose",
        execution_profile_ref="omnigent-codex@1",
    )


@pytest.mark.asyncio
async def test_default_switch_supersedes_old_version_and_preserves_historical_resolution(
    policy_session,
) -> None:
    service = OmnigentPolicyService(policy_session)
    first = await service.create(
        policy_id="operator-policy",
        name="Operator policy",
        owner_user_id=None,
        visibility="deployment",
        document=_document(),
        actor="owner",
    )
    await service.transition(
        policy_id="operator-policy",
        version=first.version,
        state=PolicyState.ACTIVE,
        actor="owner",
        make_default=True,
    )
    changed = deepcopy(first.document_json)
    changed["resources"]["concurrency"] = 2
    second = await service.new_version(
        policy_id="operator-policy",
        document=PolicyDocument.model_validate(changed),
        actor="owner",
        expected_parent_ref="operator-policy@1",
    )
    await service.transition(
        policy_id="operator-policy",
        version=second.version,
        state=PolicyState.ACTIVE,
        actor="owner",
        make_default=True,
    )

    policy = await service.get_policy("operator-policy")
    historical = await service.resolve_ref("operator-policy@1")
    assert policy.default_version == 2
    assert historical.state == PolicyState.SUPERSEDED.value
    assert historical.document_json["resources"]["concurrency"] == 1
    assert second.supersedes_ref == "operator-policy@1"


@pytest.mark.asyncio
async def test_disabling_default_clears_selection_without_deleting_history(
    policy_session,
) -> None:
    service = OmnigentPolicyService(policy_session)
    row = await service.create(
        policy_id="disable-policy",
        name="Disable policy",
        owner_user_id=None,
        visibility="deployment",
        document=_document(),
        actor="owner",
    )
    await service.transition(
        policy_id="disable-policy",
        version=row.version,
        state=PolicyState.ACTIVE,
        actor="owner",
        make_default=True,
    )
    await service.transition(
        policy_id="disable-policy",
        version=row.version,
        state=PolicyState.DISABLED,
        actor="owner",
    )

    assert (await service.get_policy("disable-policy")).default_version is None
    assert (await service.resolve_ref("disable-policy@1")).state == "disabled"


@pytest.mark.asyncio
async def test_bootstrap_activates_safe_defaults_and_rejects_deployment_drift(
    policy_session,
) -> None:
    assert await seed_bootstrap_policies(policy_session) == [
        "omnigent-codex",
        "codex-static",
        "codex-on-demand",
    ]
    for policy_id in ("omnigent-codex", "codex-static", "codex-on-demand"):
        policy = await policy_session.get(OmnigentPolicy, policy_id)
        version = await OmnigentPolicyService(policy_session).get_version(policy_id, 1)
        assert policy.default_version == 1
        assert version.state == PolicyState.ACTIVE.value
        assert version.validation_json["valid"] is True

    version = await OmnigentPolicyService(policy_session).get_version("codex-static", 1)
    version.digest = "sha256:" + "0" * 64
    await policy_session.commit()
    with pytest.raises(PolicyConflict, match="conflicts with deployment authority"):
        await seed_bootstrap_policies(policy_session)


def test_policy_version_has_no_delete_cascade_from_identity() -> None:
    foreign_key = next(iter(OmnigentPolicyVersion.__table__.c.policy_id.foreign_keys))
    assert foreign_key.ondelete is None
