"""Route-level canonical turn admission for native Workflow Chat.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane] route all
continuations, remediation, checkpoints, and chat through canonical sessions and
turn attempts), acceptance criterion 9.

``tests/unit/api/routers/test_omnigent_workflow_chat.py`` covers the facade with a
*pre-canonical* binding, where the boundary correctly finds no canonical session
and leaves the legacy path untouched. This module drives the real HTTP composer
route with a canonical session present, so admission, the fenced
``omnigent.submit_turn`` command, and the refusal-to-HTTP mapping are exercised
end-to-end at the route boundary rather than at the helper.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers import omnigent_bridge
from api_service.db.models import Base
from moonmind.omnigent.control_plane import (
    IMMUTABLE_AUTHORITY_METADATA_KEY,
    OmnigentControlPlaneStore,
)
from moonmind.omnigent.turn_contracts import ImmutableExecutionAuthority
from tests.unit.api.routers.test_omnigent_workflow_chat import (
    _CHAT_BINDING_ID,
    _USER_ID,
    _build,
    _path,
)

CANONICAL_SESSION_ID = "oms_route_1"
RECORDED_AUTHORITY = ImmutableExecutionAuthority(
    executionPlanRef="artifact://intent/route",
    providerProfileId="profile-1",
)


@pytest_asyncio.fixture()
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/route_turns.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield OmnigentControlPlaneStore(factory)
    await engine.dispose()


async def _establish(store, *, actor: str = str(_USER_ID)):
    session, _turn = await store.establish_session(
        session_id=CANONICAL_SESSION_ID,
        moonmind_workflow_id="mm:w1",
        provider="omnigent",
        provider_session_ref="prov-sess-1",
        chat_binding_id=_CHAT_BINDING_ID,
        first_turn_attempt_id=f"{CANONICAL_SESSION_ID}-t0",
        first_turn_idempotency_key=f"{CANONICAL_SESSION_ID}-idem-0",
        step_execution_id="step-1",
        metadata={
            "actorId": actor,
            IMMUTABLE_AUTHORITY_METADATA_KEY: RECORDED_AUTHORITY.as_dict(),
        },
    )
    # Live runtime authority: the facade observes an attached, resumable
    # provider session on a current host and credential generation.
    async with store.transaction() as repos:
        await repos.sessions.bind_runtime_authority(
            CANONICAL_SESSION_ID,
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            provider_profile_id="profile-1",
            provider_profile_generation=1,
            host_binding_ref="host-binding-1",
            host_lease_ref="host-lease-1",
            credential_generation=1,
        )
    return session


@pytest.fixture()
def canonical_control_plane(store, monkeypatch: pytest.MonkeyPatch):
    """Point the router's one control-plane seam at the canonical store."""

    monkeypatch.setattr(omnigent_bridge, "_control_plane_store", lambda: store)
    return store


@pytest.mark.asyncio
async def test_composer_message_route_creates_one_fenced_canonical_turn(
    store, canonical_control_plane
) -> None:
    """An admitted chat message is a canonical turn plus one fenced command.

    The route is the production entrypoint: the message is admitted on the one
    canonical session that owns the chat binding *before* the bridge control
    journal records its transport claim, so the bridge can never be a second
    submission authority. A browser retry under the same key stays one logical
    turn and one command.
    """

    await _establish(store)
    client, proxy, _bridge = _build()

    for _attempt in range(2):
        response = client.post(
            _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
            json={"type": "message", "text": "continue the work"},
            headers={"Idempotency-Key": "route-turn-1"},
        )
        assert response.status_code == 200, response.text

    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session(CANONICAL_SESSION_ID)
        commands = await repos.commands.list_for_session(CANONICAL_SESSION_ID)
        session = await repos.sessions.get(CANONICAL_SESSION_ID)

    chat_turns = [item for item in turns if item.turn_source == "workflow_chat"]
    assert len(chat_turns) == 1
    submits = [
        command
        for command in commands
        if command.command_type == "omnigent.submit_turn"
    ]
    assert len(submits) == 1
    # The command is fenced to the session generation it was admitted under, and
    # the admitted turn becomes the session's active turn.
    assert submits[0].fencing_generation == session.fencing_generation
    assert session.active_turn_attempt_id == chat_turns[0].turn_attempt_id
    # One canonical session and one chat binding, whatever the retry count.
    assert session.chat_binding_id == _CHAT_BINDING_ID
    # The provider still received exactly one forwarded turn.
    assert len([event for event in proxy.posted if event["type"] == "message"]) == 1


@pytest.mark.asyncio
async def test_composer_message_route_maps_a_terminal_session_to_409(
    store, canonical_control_plane
) -> None:
    """A refused admission is a typed 409 and never reaches the provider."""

    await _establish(store)
    async with store.transaction() as repos:
        current = await repos.sessions.get(CANONICAL_SESSION_ID)
        await repos.sessions.mark_terminal(
            CANONICAL_SESSION_ID,
            "completed",
            expected_revision=current.revision,
            expected_fencing_generation=current.fencing_generation,
        )
    client, proxy, _bridge = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "text": "one more thing"},
        headers={"Idempotency-Key": "route-turn-terminal"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "omnigent_chat_session_read_only"
    assert proxy.posted == []
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session(CANONICAL_SESSION_ID)
        decisions = await repos.decisions.list_for_session(CANONICAL_SESSION_ID)
    # Refused: no turn row, but the refusal is durable evidence.
    assert [item.turn_source for item in turns] == ["initial"]
    assert decisions[-1].product_visible_transition == "new_session_required"
    assert decisions[-1].reason_code == "session_terminal"


@pytest.mark.asyncio
async def test_composer_message_route_refuses_a_cross_user_submission(
    store, canonical_control_plane
) -> None:
    """A caller who does not own the canonical session is refused pre-mutation."""

    await _establish(store, actor="someone-else")
    client, proxy, _bridge = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "text": "not my session"},
        headers={"Idempotency-Key": "route-turn-cross-user"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "omnigent_chat_operation_denied"
    assert proxy.posted == []
    async with store.transaction() as repos:
        decisions = await repos.decisions.list_for_session(CANONICAL_SESSION_ID)
    assert decisions[-1].reason_code == "actor_not_session_owner"


@pytest.mark.asyncio
async def test_lifecycle_controls_are_not_turns_at_the_route_boundary(
    store, canonical_control_plane
) -> None:
    """Interrupt and stop change lifecycle, so they create no turn attempt."""

    await _establish(store)
    client, _proxy, _bridge = _build()

    for control in ("interrupt", "stop"):
        response = client.post(
            _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
            json={"type": control},
            headers={"Idempotency-Key": f"route-control-{control}"},
        )
        assert response.status_code in {200, 403}, response.text

    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session(CANONICAL_SESSION_ID)
    assert [item.turn_source for item in turns] == ["initial"]
