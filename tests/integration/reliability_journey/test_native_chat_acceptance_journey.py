"""Deterministic native Workflow Chat acceptance journey (MoonLadderStudios/MoonMind#3642).

This drives the *real* MoonMind product/authority boundaries for the native
Workflow Chat journey — the binding-scoped native-UI serving router, the
binding-scoped facade primitives (route allowlist, identity-substitution guard,
immutable capability policy), and the high-security outbound scan — against a
controllable fake Omnigent upstream. It then feeds the deterministic-lane
outcomes into the fail-closed acceptance gate
(:func:`moonmind.omnigent.native_chat_acceptance.build_native_chat_acceptance_report`)
together with well-formed protected-live evidence, and proves the gate:

* builds a single passing report only when every required scenario passes in its
  expected lane; and
* refuses publication when a real deterministic-lane row regresses.

The fake upstream lets the suite assert every upstream request and the credential
boundary without replacing the decisive MoonMind binding, proxy, policy, scan,
serving, and evidence boundaries — those run as production code.

Only the deterministic lane runs here (hermetic, required-CI-safe). The
protected stock-image lane (§8) runs against immutable stock images with a real
enrolled Provider Profile in a separate credentialed job; this suite supplies
well-formed protected-live evidence so the gate contract is exercised end to end.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.omnigent_bridge import (
    _get_bridge_store,
    _get_execution_service,
    _require_bridge_enabled,
)
from api_service.api.routers.omnigent_native_ui import (
    NATIVE_UI_MOUNT_PATH,
    NativeUiUpstreamError,
    NativeUiUpstreamResponse,
    get_native_ui_upstream,
    native_ui_router,
)
from api_service.auth_providers import get_current_user
from moonmind.omnigent.native_chat_acceptance import (
    CASE_EVIDENCE_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    REQUIRED_SCENARIOS,
    SCENARIO_BINDING_AUTHORIZATION,
    SCENARIO_CAPABILITY_POLICY,
    SCENARIO_CREDENTIAL_ISOLATION,
    SCENARIO_DETERMINISTIC_JOURNEY,
    SCENARIO_LANES,
    SCENARIO_OUTBOUND_SCAN,
    build_native_chat_acceptance_report,
)
from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.native_chat_rollout import (
    NativeChatRolloutMode,
    resolve_native_chat_rollout,
)
from moonmind.omnigent.native_outbound_scan import (
    NativeScanBlockedError,
    NativeScanSurface,
    scan_native_outbound,
)
from moonmind.omnigent.native_ui import NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION
from moonmind.omnigent.workflow_chat_facade import (
    CAP_CONTROL_UNSUPPORTED,
    CAP_SEND_MESSAGE,
    WorkflowChatFacadeError,
    assert_no_identity_substitution,
    match_facade_operation,
    recompute_capabilities,
    required_capability_for_event,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_USER_ID = uuid4()
_CHAT_BINDING_ID = "chatb_journey_1"
_PROVIDER_SESSION_ID = "prov-sess-secret-do-not-leak"
_UPSTREAM_ORIGIN = "https://omnigent.internal:8000"
_NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)

_INDEX_HTML = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    '<script type="module" src="/assets/index-abc.js"></script>'
    "</head><body><div id=\"root\"></div></body></html>"
).encode("utf-8")


# --- Fake upstream + product-path harness ------------------------------------


class _FakeUpstream:
    """Controllable fake Omnigent upstream; records every request path."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.paths: list[str] = []
        self._unavailable = unavailable

    async def fetch(self, path: str) -> NativeUiUpstreamResponse:
        self.paths.append(path)
        if self._unavailable:
            raise NativeUiUpstreamError("upstream unavailable")
        if path == "/assets/index-abc.js":
            return NativeUiUpstreamResponse(
                status_code=200,
                content=b"console.log('native-app');",
                media_type="application/javascript",
            )
        return NativeUiUpstreamResponse(
            status_code=200, content=_INDEX_HTML, media_type="text/html"
        )


def _row(**overrides: Any) -> SimpleNamespace:
    values = dict(
        bridge_session_id="brs-internal-1",
        moonmind_workflow_id="mm:w1",
        moonmind_run_id="run-1",
        moonmind_agent_run_id="ar-1",
        status="active",
        omnigent_session_id=_PROVIDER_SESSION_ID,
        metadata_={},
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeStore:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def get_session_by_chat_binding_id(self, chat_binding_id: str):
        return self._row


class _FakeService:
    def __init__(self, owner_id: Any) -> None:
        self._owner_id = owner_id

    async def describe_execution(self, workflow_id: str):
        return SimpleNamespace(owner_id=self._owner_id)


_UNSET = object()


def _client(*, owner_id: Any = _USER_ID, row: Any = _UNSET, upstream: _FakeUpstream | None = None):
    app = FastAPI()
    app.include_router(native_ui_router, prefix=NATIVE_UI_MOUNT_PATH)
    upstream = upstream or _FakeUpstream()
    config = SimpleNamespace(enabled=True, host_protocol_mode="upstream")
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=_USER_ID, email="chat@example.com", is_superuser=False
    )
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(owner_id)
    app.dependency_overrides[_get_bridge_store] = lambda: _FakeStore(
        _row() if row is _UNSET else row
    )
    app.dependency_overrides[_require_bridge_enabled] = lambda: config
    app.dependency_overrides[get_native_ui_upstream] = lambda: upstream
    return TestClient(app), upstream


def _scoped(suffix: str = "", *, binding: str = _CHAT_BINDING_ID) -> str:
    base = f"{NATIVE_UI_MOUNT_PATH}/{binding}"
    return f"{base}/{suffix}" if suffix else base


# --- Real deterministic-lane boundary checks ---------------------------------


def _prove_deterministic_browser_journey() -> None:
    """Steps 1-4: open Workflow Detail chat, resolve binding, load embedded UI."""

    client, upstream = _client()
    response = client.get(_scoped(), params={"embedded": "1"})
    assert response.status_code == 200
    body = response.text
    assert 'id="root"' in body  # the native app shell, not a MoonMind copy
    assert "window.__MOONMIND_OMNIGENT_CHAT__=" in body
    assert NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION in body
    assert f'"chatBindingId":"{_CHAT_BINDING_ID}"' in body
    assert '"mode":"embedded"' in body
    # The browser only ever fetched the scoped index; every upstream request is
    # observed through the MoonMind serving boundary.
    assert upstream.paths == ["/"]


def _prove_credential_and_network_isolation() -> None:
    """§3: no provider identity/credential leaks into the served document."""

    client, _ = _client()
    response = client.get(_scoped(), params={"embedded": "1"})
    body = response.text
    assert _PROVIDER_SESSION_ID not in body
    assert "prov-sess" not in body
    assert _UPSTREAM_ORIGIN not in body
    # connect-src stays on the MoonMind origin so provider JS cannot open a
    # direct upstream fetch/XHR/EventSource/WebSocket.
    assert "connect-src 'self'" in response.headers["content-security-policy"]


def _prove_binding_authorization_isolation() -> None:
    """§2: possessing a binding id / provider-shaped id grants no authority."""

    # Unauthorized caller and unknown binding both collapse to a non-enumerating
    # 404 (never reveals whether a binding or provider session exists).
    unauth, _ = _client(owner_id=uuid4())
    assert unauth.get(_scoped(), params={"embedded": "1"}).status_code == 404
    unknown, _ = _client(row=None)
    assert unknown.get(_scoped("assets/index-abc.js")).status_code == 404

    # The facade route allowlist rejects a non-allowlisted upstream route.
    assert match_facade_operation("POST", "v1/sessions/x/danger") is None
    assert match_facade_operation("GET", f"v1/sessions/{_CHAT_BINDING_ID}") is not None

    # Session/identity substitution fails closed across path, body, and header.
    with pytest.raises(WorkflowChatFacadeError):
        assert_no_identity_substitution(
            chat_binding_id=_CHAT_BINDING_ID,
            path_session_id="some-other-session",
        )
    with pytest.raises(WorkflowChatFacadeError):
        assert_no_identity_substitution(
            chat_binding_id=_CHAT_BINDING_ID,
            path_session_id=_CHAT_BINDING_ID,
            body={"content": {"provider_session_id": "sneaky"}},
        )
    with pytest.raises(WorkflowChatFacadeError):
        assert_no_identity_substitution(
            chat_binding_id=_CHAT_BINDING_ID,
            path_session_id=_CHAT_BINDING_ID,
            headers={"x-omnigent-endpoint": _UPSTREAM_ORIGIN},
        )
    # A clean, in-scope request passes.
    assert_no_identity_substitution(
        chat_binding_id=_CHAT_BINDING_ID,
        path_session_id=_CHAT_BINDING_ID,
        body={"type": "message", "content": "hello"},
    )


def _prove_capability_policy_immutability() -> None:
    """§4: pinned/immutable capability policy and active-vs-terminal controls."""

    active = recompute_capabilities("active")
    assert active[CAP_SEND_MESSAGE] is True
    # Model/effort/goal/terminal/workspace authority is never granted to the browser.
    for denied in ("changeModel", "changeEffort", "changeGoal", "createTerminal", "mutateWorkspace"):
        assert active[denied] is False

    # Terminal sessions are read-only: mutations are withdrawn.
    terminal = recompute_capabilities("completed")
    assert terminal[CAP_SEND_MESSAGE] is False

    # A stored policy can only remove authority, never add it.
    policy_off = recompute_capabilities("active", policy_capabilities={CAP_SEND_MESSAGE: False})
    assert policy_off[CAP_SEND_MESSAGE] is False

    # An unsupported/destructive control maps to a capability the facade never
    # grants (a sendMessage holder cannot reach stop/cleanup/harvest).
    assert required_capability_for_event("cleanup_session") == CAP_CONTROL_UNSUPPORTED
    assert required_capability_for_event("message") == CAP_SEND_MESSAGE


def _prove_high_security_outbound_scan() -> None:
    """§5: high-security scan blocks secret-like native payloads before upstream."""

    # A clean message is allowed.
    clean = scan_native_outbound(
        surface=NativeScanSurface.MESSAGE,
        body={"type": "message", "content": "please refactor the parser"},
        high_security_mode=True,
    )
    assert clean.allowed is True

    # A secret-like payload is blocked with only redacted category/location.
    with pytest.raises(NativeScanBlockedError) as blocked:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE,
            body={"type": "message", "content": "token=ghp_" + "a" * 36},
            high_security_mode=True,
        )
    metadata = blocked.value.evidence.audit_metadata()
    assert metadata["scanOutcome"] == "block"
    # No detected value leaks into the evidence.
    assert "ghp_" not in str(metadata)


def _prove_diagnostic_fallback() -> None:
    """§7: upstream-unavailable serves a truthful unavailable state, not a composer."""

    client, upstream = _client(upstream=_FakeUpstream(unavailable=True))
    response = client.get(_scoped(), params={"embedded": "1"})
    assert response.status_code == 503
    assert "native_ui_upstream_unavailable" in response.text
    # No interactive custom composer is fabricated.
    assert "textarea" not in response.text.lower()
    assert upstream.paths == ["/"]


def _prove_telemetry_and_rollout() -> None:
    """§10: rollback and canary gating keep native Chat gated appropriately."""

    read_only = resolve_native_chat_rollout(
        mode=NativeChatRolloutMode.READ_ONLY, acceptance_recorded=True
    )
    assert read_only.serve_native_ui is False and read_only.read_only_fallback is True

    canary_gated = resolve_native_chat_rollout(
        mode=NativeChatRolloutMode.CANARY, acceptance_recorded=False
    )
    assert canary_gated.serve_native_ui is False

    canary_ok = resolve_native_chat_rollout(
        mode=NativeChatRolloutMode.CANARY, acceptance_recorded=True
    )
    assert canary_ok.serve_native_ui is True


_DETERMINISTIC_PROOFS = {
    SCENARIO_DETERMINISTIC_JOURNEY: _prove_deterministic_browser_journey,
    SCENARIO_CREDENTIAL_ISOLATION: _prove_credential_and_network_isolation,
    SCENARIO_BINDING_AUTHORIZATION: _prove_binding_authorization_isolation,
    SCENARIO_CAPABILITY_POLICY: _prove_capability_policy_immutability,
    SCENARIO_OUTBOUND_SCAN: _prove_high_security_outbound_scan,
    "diagnostic-fallback": _prove_diagnostic_fallback,
    "native-ui-and-transports": _prove_deterministic_browser_journey,
    "telemetry-and-rollout": _prove_telemetry_and_rollout,
}


# --- Evidence assembly for the gate ------------------------------------------


def _identities() -> dict[str, Any]:
    digest = "b" * 64
    return {
        "moonmindCommit": "journeycommit",
        "moonmindBuild": "journey-build",
        "hostArchitecture": "linux/amd64",
        "contractVersions": {
            "nativeUiBootstrap": NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
            "nativeUiRouteFeature": "1",
            "outboundScan": "moonmind.omnigent.native_outbound_scan.v1",
            "telemetry": "moonmind.omnigent.native_chat_telemetry/v1",
        },
        "images": {
            "server": f"server@sha256:{digest}",
            "ui": f"ui@sha256:{digest}",
            "host": f"host@sha256:{digest}",
        },
        "compatibilityManifestDigest": f"sha256:{digest}",
    }


def _acceptance_source(*, proven: set[str]) -> dict[str, Any]:
    identities = _identities()
    evidence_objects: dict[str, Any] = {}

    def _register(name: str, claim: str) -> None:
        case_ref = f"artifact://case/{name}"
        evidence_objects[f"artifact://{name}"] = {
            "schemaVersion": EVIDENCE_SCHEMA_VERSION,
            "claim": claim,
            "status": "passed",
            "identities": copy.deepcopy(identities),
            "evidenceRefs": [f"artifact://channel/{name}"],
            "cases": {"controlling-case": {"status": "passed", "evidenceRefs": [case_ref]}},
            "generatedAt": "2026-07-21T00:00:00Z",
            "expiresAt": "2026-07-22T00:00:00Z",
            "revokedAt": None,
            "supersededBy": None,
            "producer": "reliability-journey:native-chat",
            "secretScan": "passed",
            "cleanup": "passed",
        }
        evidence_objects[case_ref] = {
            "schemaVersion": CASE_EVIDENCE_SCHEMA_VERSION,
            "claim": claim,
            "case": "controlling-case",
            "status": "passed",
            "identities": copy.deepcopy(identities),
            "evidenceRefs": [f"artifact://channel/case/{name}"],
            "generatedAt": "2026-07-21T00:00:00Z",
            "expiresAt": "2026-07-22T00:00:00Z",
            "revokedAt": None,
            "supersededBy": None,
            "producer": "reliability-journey:native-chat",
            "secretScan": "passed",
            "cleanup": "passed",
        }

    scenarios: dict[str, Any] = {}
    for name in REQUIRED_SCENARIOS:
        status = "passed" if name in proven else "failed"
        _register(name, f"scenario:{name}")
        scenarios[name] = {
            "status": status,
            "lane": SCENARIO_LANES[name],
            "evidenceRefs": [f"artifact://{name}"],
        }
    _register("cleanup", "cleanup")

    return {
        "producer": "reliability-journey:native-chat",
        "expiresAt": "2026-07-22T00:00:00Z",
        "supersedes": None,
        "identities": identities,
        "safeIdentities": {
            "workflowRef": "wf-ref-journey",
            "runRef": "run-ref-journey",
            "stepRef": "step-ref-journey",
            "agentRunRef": "agentrun-ref-journey",
            "bindingRef": _CHAT_BINDING_ID,
        },
        "profilePolicyRefs": {
            "profileRef": "oauth-1",
            "launchPolicyRef": "codex-on-demand@1",
            "effectiveLaunchSnapshotRef": "artifact://effective-launch/journey",
            "providerProfileRef": "provider-profile-journey",
        },
        "scenarios": scenarios,
        "cleanup": {
            "status": "passed",
            "evidenceRefs": ["artifact://cleanup"],
            "historicalEvidencePreserved": True,
            "leasesReleased": True,
        },
        "secretScan": {"status": "passed"},
        "evidenceObjects": evidence_objects,
    }


def test_native_chat_acceptance_journey_gates_rollout() -> None:
    # 1. Run the real deterministic-lane boundary proofs. Each raises on failure,
    #    so reaching the assembly step means the deterministic rows genuinely
    #    passed against production MoonMind code.
    proven: set[str] = set()
    for scenario, proof in _DETERMINISTIC_PROOFS.items():
        proof()
        proven.add(scenario)

    # 2. The protected-live rows run in a separate credentialed job; supply
    #    well-formed evidence so the full gate contract is exercised.
    for name, lane in SCENARIO_LANES.items():
        if lane == "protected_live":
            proven.add(name)

    assert proven == set(REQUIRED_SCENARIOS)

    # 3. The complete matrix builds one passing gate report.
    report = build_native_chat_acceptance_report(
        _acceptance_source(proven=proven), now=_NOW
    )
    assert report["status"] == "passed"
    assert report["issue"] == "MoonLadderStudios/MoonMind#3642"

    # 4. Fail-closed: if a real deterministic row regresses, the gate refuses to
    #    publish (implementation PRs are never enough on their own).
    regressed = set(proven)
    regressed.discard(SCENARIO_OUTBOUND_SCAN)
    with pytest.raises(ConformanceContractError):
        build_native_chat_acceptance_report(
            _acceptance_source(proven=regressed), now=_NOW
        )
