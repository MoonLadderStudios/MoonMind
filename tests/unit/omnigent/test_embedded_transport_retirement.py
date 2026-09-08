"""Retirement guard for the experimental embedded host transport.

MoonLadderStudios/MoonMind#3955 retired ``embedded_omnigent_compatible_server``:
new embedded-transport admission is rejected at the trusted selection boundary
without silently substituting proxy mode, while retained sessions keep their
recorded mode/cleanup owner and readable history, native Workflow Chat keeps
its unrelated ``embedded=1`` presentation option, and shared
bridge/session/event/credential contracts are preserved.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

RETIRED_LAUNCH_MODULES = (
    "moonmind.omnigent.bridge_embedded",
    "moonmind.omnigent.embedded_host_channel",
    "moonmind.omnigent.embedded_evidence",
    "moonmind.omnigent.embedded_acceptance",
)

RETIRED_STORE_LAUNCH_METHODS = (
    "record_embedded_host_lifecycle",
    "bind_embedded_runner",
    "begin_embedded_runner_launch",
    "fail_embedded_runner_launch",
    "record_embedded_runner_exit",
    "mark_embedded_runner_state",
    "prepare_embedded_runner_replacement",
    "get_session_by_runner_id",
    "get_active_session_by_runner_identity",
    "list_embedded_host_readiness",
)

RETAINED_DISPOSITION_PROBES = (
    "active_host_protocol_modes",
    "embedded_reconciliation_host_lease_refs",
    "cleanup_required_host_lease_refs",
    "record_terminal_cleanup",
)


def test_retired_launch_modules_are_unavailable() -> None:
    for module in RETIRED_LAUNCH_MODULES:
        assert importlib.util.find_spec(module) is None, (
            f"{module} must stay removed: proxy production wiring cannot depend "
            "on the retired embedded launch path"
        )


def test_proxy_production_wiring_imports_without_retired_modules() -> None:
    import sys

    for module in RETIRED_LAUNCH_MODULES:
        sys.modules.pop(module, None)
    import api_service.api.routers.omnigent_bridge as bridge_router
    import api_service.api.routers.omnigent_bridge_composition as composition
    import api_service.api.routers.omnigent_agent_profiles as agent_profiles
    import api_service.api.routers.omnigent_catalog as catalog
    import moonmind.omnigent.bridge_config as bridge_config
    import moonmind.omnigent.bridge_proxy as bridge_proxy
    import moonmind.omnigent.bridge_store as bridge_store

    assert bridge_config.HOST_PROTOCOL_MODE_PROXY == "upstream_omnigent_server_proxy"
    for module in (
        bridge_router,
        composition,
        agent_profiles,
        catalog,
        bridge_proxy,
        bridge_store,
    ):
        assert module is not None
    for retired in RETIRED_LAUNCH_MODULES:
        assert retired not in sys.modules


def test_retired_transport_names_are_gone_from_production_wiring() -> None:
    import api_service.api.routers.omnigent_bridge as bridge_router
    import api_service.api.routers.omnigent_bridge_composition as composition

    for name in (
        "build_embedded_host_facade",
        "verify_embedded_host_request",
        "evaluate_embedded_host_auth_readiness",
        "connected_host_frame_is_authorized",
        "embedded_host_auth_preflight",
    ):
        assert not hasattr(bridge_router, name), name
        assert not hasattr(composition, name), name
    for name in (
        "register_embedded_omnigent_host",
        "embedded_omnigent_host_tunnel",
        "embedded_omnigent_runner_tunnel",
        "heartbeat_embedded_omnigent_host",
        "ingest_embedded_omnigent_host_event",
    ):
        assert not hasattr(bridge_router, name), name
    # The retired paths stay registered as explicit 410 Gone sentinels.
    for name in (
        "retired_embedded_host_registration",
        "retired_embedded_host_tunnel",
        "retired_embedded_runner_tunnel",
        "retired_embedded_host_heartbeat",
        "retired_embedded_host_session_event",
    ):
        assert hasattr(bridge_router, name), name


def test_store_admits_no_new_embedded_launches() -> None:
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore

    for name in RETIRED_STORE_LAUNCH_METHODS:
        assert not hasattr(OmnigentBridgeSessionStore, name), name


def test_store_keeps_drain_and_historical_read_probes() -> None:
    from moonmind.omnigent.bridge_store import (
        EMBEDDED_LIFECYCLE_KEY,
        OmnigentBridgeSessionStore,
        _advance_embedded_lifecycle,
    )

    for name in RETAINED_DISPOSITION_PROBES:
        assert hasattr(OmnigentBridgeSessionStore, name), name
    # Shared first-message idempotency still decodes retained lifecycle state.
    assert EMBEDDED_LIFECYCLE_KEY == "embedded_runner_lifecycle"
    assert callable(_advance_embedded_lifecycle)
    for name in ("mark_prepared", "mark_posting", "mark_posted", "mark_terminal"):
        assert hasattr(OmnigentBridgeSessionStore, name), name


def test_native_chat_presentation_option_survives_retirement() -> None:
    """The unrelated ``embedded=1`` UI presentation option must survive."""

    native_chat = (
        REPO_ROOT / "frontend" / "src" / "entrypoints" / "WorkflowChatNative.tsx"
    )
    assert native_chat.is_file()
    assert "embedded" in native_chat.read_text(encoding="utf-8")
