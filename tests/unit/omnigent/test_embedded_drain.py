"""Bounded drain disposition for the retired embedded host transport.

MoonLadderStudios/MoonMind#3955 (remediation): Stage 1 disabled new admission
while in-flight sessions drain. These tests pin the bounded, side-effect-free
half of the remaining work:

* read-only decoding of retained rows (recorded mode, lifecycle state,
  launch-record and cleanup-authority presence) without a live transport and
  without projecting identities or secrets;
* a bounded drain summary that reports drained only when durable counts are
  zero *and* historical decoding is available — missing evidence blocks,
  never drains;
* the drain module itself never imports the launch/store/channel/evidence/DB
  modules a later removal stage will delete.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from moonmind.omnigent.bridge_config import (
    EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID,
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
)
from moonmind.omnigent.embedded_drain import (
    EMBEDDED_DRAIN_CONTRACT_VERSION,
    EMBEDDED_LAUNCH_MODULES,
    EmbeddedDrainError,
    decode_embedded_retained_session,
    summarize_embedded_drain,
)

DRAIN_MODULE = Path(__file__).resolve().parents[3] / "moonmind/omnigent/embedded_drain.py"

# Modules a later removal stage will delete (or drain-only restrict). The
# read-only decoder must never require them: historical data does not need a
# live transport.
_FORBIDDEN_DRAIN_IMPORTS = frozenset(
    {
        *EMBEDDED_LAUNCH_MODULES,
        "moonmind.omnigent.bridge_embedded",
        "moonmind.omnigent.embedded_host_channel",
        "moonmind.omnigent.embedded_evidence",
        "moonmind.omnigent.bridge_store",
        "moonmind.omnigent.bridge_proxy",
        "api_service.db.models",
        "sqlalchemy",
    }
)


def _embedded_metadata(**overrides):
    metadata = {
        "hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED,
        "embedded_runner_launch": {"runnerId": "should-not-be-projected"},
        "embedded_runner_lifecycle": {
            "version": 1,
            "state": "runner_tunnel_ready",
            "updatedAt": "2026-09-07T00:00:00+00:00",
        },
        "egress_cleanup_authority": {"phase": "attested"},
    }
    metadata.update(overrides)
    return metadata


# ------------------------------------------------------- read-only decoding


def test_decode_projects_recorded_mode_state_and_retained_flags() -> None:
    decoded = decode_embedded_retained_session(_embedded_metadata())

    assert decoded["recordedMode"] == HOST_PROTOCOL_MODE_EMBEDDED
    assert decoded["isEmbedded"] is True
    assert decoded["lifecycleState"] == "runner_tunnel_ready"
    assert decoded["hasLaunchRecord"] is True
    assert decoded["hasCleanupAuthority"] is True
    assert decoded["retirementPathId"] == EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID


def test_decode_never_projects_identities_endpoints_or_secrets() -> None:
    metadata = _embedded_metadata()
    metadata["embedded_runner_lifecycle"] = {
        "version": 1,
        "state": "running",
        "hostId": "host-secret",
        "runnerId": "runner-secret",
        "sessionId": "session-secret",
        "timeline": [{"state": "running", "code": "ok"}],
    }
    metadata["upstreamEndpoint"] = "https://internal.example/secret"
    metadata["credential"] = "token-secret"

    decoded = decode_embedded_retained_session(metadata)

    rendered = repr(sorted(decoded.items()))
    for secret in (
        "host-secret",
        "runner-secret",
        "session-secret",
        "internal.example",
        "token-secret",
    ):
        assert secret not in rendered
    assert set(decoded) == {
        "recordedMode",
        "isEmbedded",
        "lifecycleState",
        "hasLaunchRecord",
        "hasCleanupAuthority",
        "retirementPathId",
    }


def test_decode_handles_proxy_unknown_and_missing_keys() -> None:
    proxy = decode_embedded_retained_session({"hostProtocolMode": HOST_PROTOCOL_MODE_PROXY})

    assert proxy["recordedMode"] == HOST_PROTOCOL_MODE_PROXY
    assert proxy["isEmbedded"] is False
    assert proxy["lifecycleState"] is None
    assert proxy["hasLaunchRecord"] is False
    assert proxy["hasCleanupAuthority"] is False

    assert decode_embedded_retained_session({})["recordedMode"] == "unknown"
    assert (
        decode_embedded_retained_session({"embedded_runner_lifecycle": None})[
            "lifecycleState"
        ]
        is None
    )


def test_decode_rejects_non_mapping() -> None:
    with pytest.raises(EmbeddedDrainError):
        decode_embedded_retained_session(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_retained_keys_match_their_canonical_owners() -> None:
    pytest.importorskip("fastapi_users")
    from moonmind.omnigent import bridge_store
    from moonmind.omnigent.control_plane.identities import (
        EGRESS_CLEANUP_AUTHORITY_KEY,
    )

    assert bridge_store.EMBEDDED_LAUNCH_KEY == "embedded_runner_launch"
    assert bridge_store.EMBEDDED_LIFECYCLE_KEY == "embedded_runner_lifecycle"
    assert EGRESS_CLEANUP_AUTHORITY_KEY == "egress_cleanup_authority"


# ---------------------------------------------------------- drain summary


def test_summarize_reports_drained_only_when_fully_drained() -> None:
    disposition = summarize_embedded_drain(
        active_embedded_sessions=0,
        active_embedded_leases=0,
        historical_decoding_available=True,
    )

    assert disposition["drained"] is True
    assert disposition["blockers"] == ()
    assert disposition["retirementPathId"] == EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID
    assert (
        disposition["contractVersion"] == EMBEDDED_DRAIN_CONTRACT_VERSION
    )


def test_summarize_names_every_blocker_and_never_implies_drain() -> None:
    disposition = summarize_embedded_drain(
        active_embedded_sessions=2,
        active_embedded_leases=1,
        historical_decoding_available=False,
    )

    assert disposition["drained"] is False
    assert disposition["blockers"] == (
        "active_embedded_sessions:2",
        "active_embedded_leases:1",
        "historical_decoding_unavailable",
    )

    leases_only = summarize_embedded_drain(
        active_embedded_sessions=0, active_embedded_leases=3
    )
    assert leases_only["drained"] is False
    assert leases_only["blockers"] == ("active_embedded_leases:3",)


def test_summarize_rejects_malformed_counts() -> None:
    with pytest.raises(EmbeddedDrainError):
        summarize_embedded_drain(active_embedded_sessions=-1, active_embedded_leases=0)
    with pytest.raises(EmbeddedDrainError):
        summarize_embedded_drain(active_embedded_sessions=True, active_embedded_leases=0)  # type: ignore[arg-type]


# ---------------------------------------------------------- import boundary


def test_drain_module_imports_no_launch_store_or_db_modules() -> None:
    tree = ast.parse(DRAIN_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
    assert imported.isdisjoint(_FORBIDDEN_DRAIN_IMPORTS), sorted(
        imported & _FORBIDDEN_DRAIN_IMPORTS
    )


def test_launch_module_inventory_names_the_drain_gated_surfaces() -> None:
    assert set(EMBEDDED_LAUNCH_MODULES) == {
        "moonmind.omnigent.bridge_embedded",
        "moonmind.omnigent.embedded_host_channel",
        "moonmind.omnigent.embedded_evidence",
    }
