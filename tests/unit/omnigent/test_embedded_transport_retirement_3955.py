"""Staged retirement of the experimental embedded host transport (#3955).

MoonLadderStudios/MoonMind#3955 retires the experimental embedded host/runner
transport without removing native chat or historical evidence:

* new embedded-transport admission fails fast at the trusted bridge-config
  selection boundary with an actionable proxy alternative (never a silent
  substitution);
* the proxy-only production path never requires the retired embedded launch
  modules;
* the native Workflow Chat ``embedded=1`` presentation option and the shared
  bridge/session/event/credential contracts are preserved;
* removed configuration is detected at API/worker startup, and readiness no
  longer advertises the removed transport.

Physical removal of the launch code follows at the launch-only removal stage
once bounded drain probes establish no active or cleanup owner remains; these
tests pin the admission-disabled stage, not the deletion.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from moonmind.omnigent.bridge_config import (
    CANONICAL_FIRST_MESSAGE_STATES,
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
    OMNIGENT_BRIDGE_CONFIG_PATH_ENV,
    BridgeConfigError,
    OmnigentBridgeConfig,
    load_bridge_config,
    parse_bridge_config,
    resolve_bridge_config,
)
from moonmind.omnigent.legacy_retirement import (
    LegacyAdmissionRejected,
    RemovalStage,
    RetirementClass,
    assert_new_admission_allowed,
    evaluate_removal_eligibility,
    get_retirement_record,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

_EMBEDDED_EVIDENCE_BLOCK = {
    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy-conformance",
    "liveSmokeEvidenceRef": "artifact://omnigent/live-smoke",
    "hostAuthConformanceEvidenceRef": "artifact://omnigent/host-auth",
}


def _enabled_embedded_document() -> dict[str, Any]:
    return {
        "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
        "hostConnection": {"embedded": dict(_EMBEDDED_EVIDENCE_BLOCK)},
    }


def test_new_embedded_transport_admission_is_rejected() -> None:
    """An enabled bridge cannot select the retired embedded transport."""

    with pytest.raises(BridgeConfigError) as exc_info:
        parse_bridge_config(_enabled_embedded_document())
    message = str(exc_info.value)
    # Actionable: names the supported alternative and the owning issue, and
    # states the retired value is never silently substituted.
    assert "upstream_omnigent_server_proxy" in message
    assert "3955" in message
    assert "never silently substituted" in message


def test_embedded_mode_mismatch_still_fails_without_substitution() -> None:
    """A split-brain mode declaration fails instead of picking a mode."""

    with pytest.raises(BridgeConfigError):
        parse_bridge_config(
            {
                "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_PROXY},
                "hostConnection": {
                    "mode": HOST_PROTOCOL_MODE_EMBEDDED,
                    "embedded": dict(_EMBEDDED_EVIDENCE_BLOCK),
                },
            }
        )


def test_omitted_mode_still_resolves_to_proxy_production_path() -> None:
    """Omitted values exercise the same proxy production path as explicit ones."""

    defaulted = parse_bridge_config({})
    explicit = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_PROXY},
            "hostConnection": {"mode": HOST_PROTOCOL_MODE_PROXY},
        }
    )
    assert defaulted.host_protocol_mode == HOST_PROTOCOL_MODE_PROXY
    assert explicit.host_protocol_mode == HOST_PROTOCOL_MODE_PROXY
    assert (
        defaulted.readiness()["selectedMode"]
        == explicit.readiness()["selectedMode"]
        == HOST_PROTOCOL_MODE_PROXY
    )


def test_disabled_embedded_declaration_still_decodes_for_historical_reads() -> None:
    """The embedded literal survives for retained rows, not for new admission."""

    config = parse_bridge_config(
        {"enabled": False, **_enabled_embedded_document()}
    )
    assert config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
    assert config.readiness()["conformanceState"] == "disabled"


def test_readiness_no_longer_advertises_embedded_transport() -> None:
    """An enabled bridge reports the proxy-only topology."""

    readiness = parse_bridge_config({}).readiness()
    assert readiness["selectedMode"] == HOST_PROTOCOL_MODE_PROXY
    assert readiness["evidenceRefs"] == {}
    assert "evidenceValidation" not in readiness


def test_startup_resolution_rejects_retired_selection(tmp_path: Path) -> None:
    """An operator document selecting embedded fails fast at startup."""

    doc = tmp_path / "bridge.yaml"
    doc.write_text(
        "schemaVersion: moonmind.omnigent_bridge.v1\n"
        "enabled: true\n"
        "compatibility:\n"
        "  profile: omnigent.server.v1\n"
        "  hostUnchanged: true\n"
        "  hostProtocolMode: embedded_omnigent_compatible_server\n",
        encoding="utf-8",
    )
    with pytest.raises(BridgeConfigError, match="3955"):
        resolve_bridge_config(
            env={OMNIGENT_BRIDGE_CONFIG_PATH_ENV: str(doc)}
        )
    # The default path (no operator document) still resolves to proxy.
    assert (
        resolve_bridge_config(env={}).host_protocol_mode
        == HOST_PROTOCOL_MODE_PROXY
    )


def test_retirement_rows_disable_new_admission_but_keep_history() -> None:
    """Both embedded rows refuse new work while keeping historical reads."""

    selection = get_retirement_record("omnigent.embedded.transport_selection")
    launch = get_retirement_record("omnigent.embedded.launch_and_host_routes")
    assert selection.retirement_class is RetirementClass.NEW_ADMISSION_DISABLED
    assert launch.retirement_class is RetirementClass.NEW_ADMISSION_DISABLED
    assert selection.historical_read_dependency is True
    assert launch.historical_read_dependency is True
    # Selectors retire before launch-only code: never one unreviewable change.
    assert selection.earliest_removal_stage is RemovalStage.PRODUCT_SELECTORS
    assert launch.earliest_removal_stage is RemovalStage.LAUNCH_ONLY_CODE
    assert selection.earliest_removal_stage < launch.earliest_removal_stage
    for path_id in (
        "omnigent.embedded.transport_selection",
        "omnigent.embedded.launch_and_host_routes",
    ):
        with pytest.raises(LegacyAdmissionRejected) as exc_info:
            assert_new_admission_allowed(path_id)
        assert "no longer admits new work" in str(exc_info.value)


def test_removal_waits_for_bounded_drain_before_launch_code() -> None:
    """Launch-code removal stays blocked until every active owner drains."""

    launch = get_retirement_record("omnigent.embedded.launch_and_host_routes")
    decision = evaluate_removal_eligibility(
        launch, stage=RemovalStage.LAUNCH_ONLY_CODE
    )
    assert decision.eligible is False
    assert any(
        blocker.startswith("active_owner:") for blocker in decision.blockers
    )


def test_proxy_operates_with_embedded_launch_modules_unavailable() -> None:
    """Proxy config, contracts, and composition load with embedded gone."""

    script = "\n".join(
        [
            "import sys",
            "import importlib.abc",
            "import importlib.machinery",
            "blocked = {",
            "    'moonmind.omnigent.bridge_embedded',",
            "    'moonmind.omnigent.embedded_host_channel',",
            "    'moonmind.omnigent.embedded_evidence',",
            "    'moonmind.omnigent.embedded_acceptance',",
            "}",
            "class _Retired(importlib.abc.MetaPathFinder, importlib.abc.Loader):",
            "    def find_spec(self, name, path=None, target=None):",
            "        if name in blocked:",
            "            return importlib.machinery.ModuleSpec(name, self)",
            "        return None",
            "    def create_module(self, spec):",
            "        return None",
            "    def exec_module(self, module):",
            "        raise ImportError('retired embedded module unavailable: ' + module.__name__)",
            "sys.meta_path.insert(0, _Retired())",
            f"sys.path.insert(0, r'{REPO_ROOT}')",
            "from moonmind.omnigent.bridge_config import parse_bridge_config, HOST_PROTOCOL_MODE_PROXY",
            "config = parse_bridge_config({})",
            "assert config.host_protocol_mode == HOST_PROTOCOL_MODE_PROXY",
            "assert config.readiness()['selectedMode'] == HOST_PROTOCOL_MODE_PROXY",
            "from moonmind.omnigent.bridge_proxy import BridgeSessionCreateRequest, BridgeSessionEventRequest",
            "BridgeSessionCreateRequest(labels={'moonmind.workflow_id': 'wf-1'})",
            "BridgeSessionEventRequest(type='message')",
            "import api_service.api.routers.omnigent_bridge_composition as composition",
            "assert callable(composition.build_bridge_session_proxy)",
            "assert callable(composition.build_bridge_session_store)",
            "assert 'moonmind.omnigent.bridge_embedded' not in sys.modules",
            "print('proxy-without-embedded-ok')",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert "proxy-without-embedded-ok" in completed.stdout


def test_native_chat_embedded_presentation_flag_survives() -> None:
    """The unrelated native-chat embedded=1 option is not part of retirement."""

    from typing import get_args

    from moonmind.omnigent import native_ui

    assert "embedded" in get_args(native_ui.PresentationMode)
    assert native_ui.NATIVE_UI_MOUNT_PREFIX == "/omnigent-ui/workflow-chat"
    headers = native_ui.native_ui_security_headers(
        mode="embedded", is_document=True
    )
    assert headers

    frontend = (
        REPO_ROOT / "frontend" / "src" / "entrypoints" / "WorkflowChatNative.tsx"
    )
    assert frontend.is_file()
    assert "embedded=1" in frontend.read_text(encoding="utf-8")


def test_shared_contracts_preserved() -> None:
    """First-message, bridge, event, and credential contracts are untouched."""

    from moonmind.omnigent import bridge_store
    from moonmind.omnigent import host_auth_store

    assert tuple(CANONICAL_FIRST_MESSAGE_STATES) == (
        "not_prepared",
        "prepared",
        "posting",
        "posted",
        "terminal",
    )
    assert bridge_store.FIRST_MESSAGE_POSTED
    assert bridge_store.BRIDGE_EVENT_JOURNAL_KEY
    # Embedded lifecycle metadata stays readable for retained rows; shared
    # first-message idempotency and event persistence live beside it.
    assert bridge_store.EMBEDDED_LAUNCH_KEY == "embedded_runner_launch"
    assert hasattr(bridge_store.OmnigentBridgeSessionStore, "list_event_page")
    assert hasattr(host_auth_store.HostAuthProfileStore, "get_active")
    assert isinstance(OmnigentBridgeConfig().idempotency.first_message_state_machine, tuple)


def test_bridge_config_module_still_decodes_historical_embedded_literal() -> None:
    """The retired literal remains a valid historical value, not a selector."""

    assert HOST_PROTOCOL_MODE_EMBEDDED == "embedded_omnigent_compatible_server"
    assert HOST_PROTOCOL_MODE_PROXY == "upstream_omnigent_server_proxy"
    config = parse_bridge_config(
        {"enabled": False, **_enabled_embedded_document()}
    )
    assert config.host_connection.mode == HOST_PROTOCOL_MODE_EMBEDDED
    assert "embedded" in load_bridge_config(
        "schemaVersion: moonmind.omnigent_bridge.v1\nenabled: false\n"
        "compatibility:\n  hostProtocolMode: embedded_omnigent_compatible_server\n"
    ).host_protocol_mode
