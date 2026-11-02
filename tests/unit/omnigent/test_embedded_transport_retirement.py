"""Removed embedded transport configuration and diagnostic compatibility. The executable retirement row is gone; historical literals, stable diagnostics and the independent native presentation remain."""

from __future__ import annotations

import pytest

from moonmind.omnigent.bridge_config import (
    EMBEDDED_TRANSPORT_RETIRED_CODE,
    EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID,
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
    BridgeConfigError,
    assert_new_embedded_transport_admission_allowed,
    embedded_transport_retirement,
    embedded_transport_retirement_notice,
    parse_bridge_config,
)
from moonmind.omnigent.legacy_retirement import (
    RetirementGuardError,
    get_retirement_record,
)


def _embedded_config():
    # Retired (#3955, merged base): an enabled embedded selection fails fast
    # at parse, so drain-continuity fixtures declare the retained mode on a
    # disabled bridge; retirement is enforced at admission, not at parse.
    return parse_bridge_config(
        {
            "enabled": False,
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )


# ------------------------------------------------------- code-owned retirement


def test_removed_transport_has_no_retained_execution_adapter() -> None:
    with pytest.raises(RetirementGuardError, match="unknown retirement path"):
        get_retirement_record(EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID)


# ------------------------------------------------- parse vs admission boundary


def test_embedded_config_still_parses_for_drain_continuity() -> None:
    # A disabled bridge may still declare the retained embedded mode so
    # in-flight sessions drain and historical rows decode; retirement is
    # enforced at admission, not at parse. Enabled embedded fails fast.
    config = _embedded_config()

    assert config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED


def test_new_embedded_admission_is_rejected_with_proxy_alternative() -> None:
    with pytest.raises(BridgeConfigError) as exc_info:
        assert_new_embedded_transport_admission_allowed(_embedded_config())

    message = str(exc_info.value)
    assert HOST_PROTOCOL_MODE_PROXY in message
    assert EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID in message


def test_proxy_config_still_admits() -> None:
    assert_new_embedded_transport_admission_allowed(parse_bridge_config({}))


# ------------------------------------------------------------------ readiness


def test_retirement_projection_names_row_code_and_alternative() -> None:
    retirement = embedded_transport_retirement()

    assert retirement["newAdmissionAllowed"] is False
    assert retirement["retiredForNewWork"] is True
    assert retirement["code"] == EMBEDDED_TRANSPORT_RETIRED_CODE
    assert (
        retirement["retirementPathId"] == EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID
    )
    assert retirement["supportedAlternative"] == HOST_PROTOCOL_MODE_PROXY


def test_embedded_readiness_advertises_retirement_not_capacity() -> None:
    validation = {
        key: {
            "status": "passed",
            "supportedHostModes": ["static_compose", "on_demand_docker"],
        }
        for key in ("proxyConformance", "liveSmoke", "hostAuthConformance")
    }
    readiness = _embedded_config().readiness(evidence_validation=validation)

    # A disabled bridge declaring the retained embedded mode still projects
    # the retirement row (never capacity for new work).
    assert readiness["conformanceState"] == "disabled"
    assert readiness["retirement"] == embedded_transport_retirement()
    assert readiness["retirement"]["newAdmissionAllowed"] is False


def test_proxy_readiness_carries_no_retirement_block() -> None:
    assert "retirement" not in parse_bridge_config({}).readiness()


# ---------------------------------------------------------- startup detection


def test_retirement_notice_is_none_for_proxy() -> None:
    assert embedded_transport_retirement_notice(parse_bridge_config({})) is None


def test_retirement_notice_names_proxy_for_embedded() -> None:
    notice = embedded_transport_retirement_notice(_embedded_config())

    assert notice is not None
    assert HOST_PROTOCOL_MODE_PROXY in notice
    assert EMBEDDED_TRANSPORT_RETIRED_CODE in notice
    assert EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID in notice


# ------------------------------------------------- native chat flag preserved


def test_native_chat_embedded_presentation_mode_is_untouched() -> None:
    # The Workflow Chat ``embedded=1`` query parameter is a presentation
    # option inside MoonMind's authorized facade, not the retired transport.
    from moonmind.omnigent.native_ui import presentation_mode_from_query

    assert presentation_mode_from_query("1") == "embedded"
    assert presentation_mode_from_query(None) == "full_page"
