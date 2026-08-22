"""Protected native Workflow Chat acceptance evidence contract.

MoonLadderStudios/MoonMind#3632 is complete only when the #3642
browser-to-stock-host matrix produces fresh, immutable, independently
resolvable, secret-scanned evidence.  Repository tests and a bare ``passed``
flag are not that authority.  This module defines the compact release artifact
and validates every referenced observation before it can be used as rollout
evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from moonmind.omnigent.conformance import (
    REPORT_VERSION,
    REQUIRED_EVIDENCE_CHANNELS,
    ConformanceContractError,
    assert_secret_free,
    require_pinned_images,
)
from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES
from moonmind.omnigent.harness_platform.host_classes import (
    LaunchPolicy,
    get_launch_policy,
)
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_support_combination_key,
    validate_realizer,
)
from moonmind.omnigent.native_ui_compat import compatibility_map

WORKFLOW_CHAT_ACCEPTANCE_VERSION = "moonmind.omnigent.workflow-chat-acceptance/v2"
WORKFLOW_CHAT_CASE_EVIDENCE_VERSION = "moonmind.omnigent.workflow-chat-case-evidence/v1"
WORKFLOW_CHAT_SOURCE_RECORD_VERSION = "moonmind.omnigent.workflow-chat-source-record/v1"
WORKFLOW_CHAT_COMBINATION_VERSION = "moonmind.omnigent.workflow-chat-combination/v1"
WORKFLOW_CHAT_SCENARIO_VERSION = "moonmind.omnigent.workflow-chat-scenario/v1"
WORKFLOW_CHAT_ACCEPTANCE_ISSUE = "MoonLadderStudios/MoonMind#3642"
WORKFLOW_CHAT_PARENT_ISSUE = "MoonLadderStudios/MoonMind#3632"
WORKFLOW_CHAT_COMPATIBILITY_PROFILE = "omnigent.server.v1"
MAX_ACCEPTANCE_AGE = timedelta(days=7)

# The deployed bundles a browser actually loads. Image digests alone do not
# identify the compiled dashboard or the stock Omnigent UI bundle served through
# the binding-scoped route, so both are bound separately (issue #3642 AC8).
REQUIRED_BUNDLE_DIGESTS = ("dashboard", "omnigentUi")

# Capability groups whose advertisement is decided by declared Host Class
# features or Launch Policy authority rather than by this module.
_TERMINAL_CAPABILITIES = frozenset(
    {
        "createTerminal",
        "attachTerminal",
        "viewTerminal",
        "writeTerminal",
        "closeTerminal",
    }
)
_WORKSPACE_CAPABILITIES = frozenset({"uploadFiles", "mutateWorkspace"})

# Provider Profile classes are release identity: the credential and provider
# authority a combination ran under is pinned by the claimed inventory, so
# evidence gathered under one class can never qualify another.
_CODEX_OAUTH_PROFILE_CLASS = "omnigent-codex-oauth-profile-class@1"
_OPENCODE_API_KEY_PROFILE_CLASS = "omnigent-opencode-api-key-profile-class@1"
_CONTROL_CAPABILITY_REQUIREMENTS: Mapping[str, str] = {
    "interruptTurn": "interrupt",
    "stopSession": "terminate",
    "replaceSession": "clear_context",
}


def advertised_native_chat_capabilities(
    *, host_features: Mapping[str, bool], launch_policy: LaunchPolicy
) -> frozenset[str]:
    """Derive the native-chat capability contract a combination advertises.

    The set is computed from already-declared authority (Host Class features,
    Launch Policy control capabilities, capture, and cleanup mode) so a
    combination that advertises a different capability surface produces a
    different required coverage set instead of silently reusing a constant.
    """

    advertised: set[str] = set()
    features = host_features
    controls = set(launch_policy.controlCapabilities)
    for name in CAPABILITY_NAMES:
        if name in _TERMINAL_CAPABILITIES and not features.get("tmux"):
            continue
        if name in _WORKSPACE_CAPABILITIES and not features.get("workspaceBind"):
            continue
        required_control = _CONTROL_CAPABILITY_REQUIREMENTS.get(name)
        if required_control is not None and required_control not in controls:
            continue
        if (
            name == "harvestEvidence"
            and launch_policy.capture.get("required") is not True
        ):
            continue
        if name == "cleanupSession" and launch_policy.cleanup.get("mode") != "remove":
            continue
        advertised.add(name)
    return frozenset(advertised)


@dataclass(frozen=True, slots=True)
class WorkflowChatCombination:
    """One combination MoonMind either claims or declines for native chat.

    ``native_chat_claimed`` is code-owned release authority: a claimed
    combination must appear in the protected matrix with its own live evidence,
    and a declined combination must appear with a stable machine reason. Neither
    may be silently omitted.
    """

    combination_id: str
    harness_id: str
    host_class_ref: str
    launch_policy_ref: str
    execution_realizer_ref: str
    compose_profile: str
    compose_services: tuple[str, ...]
    native_chat_claimed: bool
    # Credential/provider authority identity is code-owned per combination:
    # evidence collected under one Provider Profile class, credential
    # materializer, or authentication mode never qualifies another.
    provider_profile_class: str = ""
    credential_materializer_ref: str = ""
    auth_mode: str = ""
    # The manifest ``images`` key that pins the host image which actually
    # executes this combination. Distinct host classes ship distinct images, so
    # a single shared ``host`` digest cannot qualify all of them.
    host_image_key: str = "host"
    # Protected conformance declares the small capability subset it promises
    # to exercise. It deliberately does not load a context-free Host Class:
    # production Host Classes are compiled only from a persisted catalog and
    # digest-pinned deployment image.
    advertised_host_features: tuple[str, ...] = ("tmux", "workspaceBind")
    unsupported_reason: str | None = None

    def __post_init__(self) -> None:
        if self.native_chat_claimed == bool(self.unsupported_reason):
            raise ConformanceContractError(
                "workflow Chat combination must either be claimed or carry one "
                f"stable unsupported reason: {self.combination_id}"
            )
        if self.native_chat_claimed and not all(
            (
                self.provider_profile_class,
                self.credential_materializer_ref,
                self.auth_mode,
                self.host_image_key,
            )
        ):
            raise ConformanceContractError(
                "workflow Chat claimed combination must name its Provider "
                "Profile class, credential materializer, authentication mode, "
                f"and pinned host image: {self.combination_id}"
            )
        validate_realizer(self.execution_realizer_ref)

    @property
    def launch_policy(self) -> LaunchPolicy:
        return get_launch_policy(self.launch_policy_ref)

    @property
    def host_mode(self) -> str:
        return self.launch_policy.hostMode

    @property
    def cleanup_mode(self) -> str:
        return str(self.launch_policy.cleanup.get("mode") or "")

    @property
    def required_cleanup_steps(self) -> tuple[str, ...]:
        """Return the ordered cleanup steps this host mode can truthfully emit.

        On-demand hosts are removed, so the live host stops. Static-connected
        hosts are drained and keep serving the next run, so demanding a stopped
        host would force the adapter to publish false evidence.
        """

        steps = _CLEANUP_STEPS_BY_MODE.get(self.cleanup_mode)
        if steps is None:
            raise ConformanceContractError(
                "workflow Chat combination has no cleanup contract for mode "
                f"{self.cleanup_mode!r}: {self.combination_id}"
            )
        return steps

    @property
    def live_resources_removed_expected(self) -> bool:
        """Whether cleanup must report the live host's resources as removed."""

        return self.cleanup_mode == "remove"

    @property
    def advertised_capabilities(self) -> frozenset[str]:
        return advertised_native_chat_capabilities(
            host_features={name: True for name in self.advertised_host_features},
            launch_policy=self.launch_policy,
        )


# Every combination MoonMind claims for native Workflow Chat, including the
# host modes whose transport and cleanup behavior differ materially (on-demand
# hosts are removed; static-connected hosts are drained and keep serving).
WORKFLOW_CHAT_COMBINATIONS: tuple[WorkflowChatCombination, ...] = (
    WorkflowChatCombination(
        combination_id="codex-on-demand-through-omnigent",
        harness_id="codex-native",
        host_class_ref="omnigent-codex-current@1",
        launch_policy_ref="codex-on-demand@1",
        execution_realizer_ref="codex-profile-bound@1",
        compose_profile="omnigent-host-codex",
        compose_services=("omnigent",),
        native_chat_claimed=True,
        provider_profile_class=_CODEX_OAUTH_PROFILE_CLASS,
        credential_materializer_ref="codex-oauth-home@1",
        auth_mode="codex-oauth",
        host_image_key="host",
    ),
    WorkflowChatCombination(
        combination_id="codex-static-connected-through-omnigent",
        harness_id="codex-native",
        host_class_ref="omnigent-codex-current@1",
        launch_policy_ref="codex-static@1",
        execution_realizer_ref="codex-profile-bound@1",
        compose_profile="omnigent-host-codex",
        compose_services=("omnigent", "omnigent-host-codex"),
        native_chat_claimed=True,
        provider_profile_class=_CODEX_OAUTH_PROFILE_CLASS,
        credential_materializer_ref="codex-oauth-home@1",
        auth_mode="codex-oauth",
        host_image_key="host",
    ),
    WorkflowChatCombination(
        combination_id="opencode-through-generic-omnigent-host",
        harness_id="opencode-native",
        host_class_ref="omnigent-opencode@1",
        launch_policy_ref="opencode-on-demand@1",
        execution_realizer_ref="generic-omnigent-host@1",
        compose_profile="omnigent-host-codex",
        compose_services=("omnigent",),
        native_chat_claimed=True,
        provider_profile_class=_OPENCODE_API_KEY_PROFILE_CLASS,
        credential_materializer_ref="opencode-auth-json@1",
        auth_mode="opencode-api-key",
        # The OpenCode journey runs the dedicated OpenCode host image, which is
        # pinned separately from the Codex host digest.
        host_image_key="opencodeHost",
    ),
)


def workflow_chat_combinations() -> dict[str, WorkflowChatCombination]:
    """Return the claimed-combination inventory keyed by combination id."""

    inventory: dict[str, WorkflowChatCombination] = {}
    for combination in WORKFLOW_CHAT_COMBINATIONS:
        if combination.combination_id in inventory:
            raise ConformanceContractError(
                "workflow Chat combination inventory has a duplicate id: "
                f"{combination.combination_id}"
            )
        inventory[combination.combination_id] = combination
    return inventory


def workflow_chat_case_id(combination_id: str, row_name: str) -> str:
    """Return the one canonical report case id for a combination's row.

    The case id is code-owned so a report cannot satisfy one combination's
    retirement coverage with another combination's passing cases.
    """

    return f"workflow-chat-{combination_id}-{row_name}"


REQUIRED_WORKFLOW_CHAT_ROWS: Mapping[str, frozenset[str]] = {
    "native-live-conversation": frozenset(
        {
            "created_through_public_executions_api",
            "workflow_chat_route_opened",
            "authoritative_binding_only",
            "native_transcript_composer_queue",
            "native_ui_primary",
            "no_custom_composer",
        }
    ),
    "scoped-transports-and-resources": frozenset(
        {
            "html_http_sse_websocket_authorized",
            "reconnect_reauthorized",
            "resources_terminals_approvals_tools_available",
            "stock_routes_exactly_covered",
            "mutation_receipts_complete",
        }
    ),
    "authority-and-security-denials": frozenset(
        {
            "alternate_binding_denied",
            "provider_session_substitution_denied",
            "hidden_control_direct_invocation_denied",
            "immutable_policy_enforced",
            "cross_user_denied",
            "cross_workflow_denied",
            "advertised_capabilities_enforced",
            "high_security_send_blocked",
            "scan_unavailable_failed_closed",
            "credentials_separated",
        }
    ),
    "terminal-evidence-and-continuation": frozenset(
        {
            "terminal_chat_read_only",
            "captured_evidence_resolved",
            "linked_continuation_created",
            "source_workflow_unchanged",
            "replay_after_stock_host_unavailable",
            "cleanup_completed_provider_profile_released_last",
        }
    ),
}
REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS: Mapping[str, frozenset[str]] = {
    "native-live-conversation": frozenset(
        {
            "browserTrace",
            "executionCreation",
            "bindingSnapshot",
            "nativeConversation",
            "nativeControls",
        }
    ),
    "scoped-transports-and-resources": frozenset(
        {
            "browserTrace",
            "facadeRequests",
            "resourceInventory",
            "mutationReceipts",
        }
    ),
    "authority-and-security-denials": frozenset(
        {
            "browserTrace",
            "denialAudit",
            "capabilitySnapshot",
            "scanAudit",
            "credentialBoundary",
        }
    ),
    "terminal-evidence-and-continuation": frozenset(
        {
            "browserTrace",
            "terminalSnapshot",
            "capturedEvidence",
            "continuationReceipt",
            "replaySnapshot",
            "cleanupReceipt",
        }
    ),
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_DIGEST_REF = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_CORRELATION_FIELDS = (
    "workflowId",
    "chatBindingId",
    "bridgeSessionId",
    "providerSessionId",
    "browserTraceId",
)
_GLOBAL_CORRELATION_FIELDS = _CORRELATION_FIELDS[:-1]
_TRANSPORTS = frozenset({"html", "http", "sse", "websocket"})
_NATIVE_UI_DOCUMENT_ROUTE = "native_ui_document"
_LINKED_CONTINUATION_RELATIONSHIP = "linked_continuation"
_NATIVE_CONTROL_KINDS = frozenset(
    {"approval", "tool", "file", "terminal", "agent", "task"}
)
_DENIAL_KINDS = frozenset(
    {
        "alternate_binding",
        "provider_session_substitution",
        "hidden_control",
        "immutable_policy",
        "cross_user",
        "cross_workflow",
    }
)
# Cleanup must finish with provider-profile release, after the live host is
# retired for its host mode, the provider session is gone, and the workspace
# result is durable. On-demand hosts stop; static-connected hosts drain and keep
# serving, so their truthful terminal step is a drain, not a stop.
_PROVIDER_PROFILE_RELEASE_STEP = "provider_profile_release"
_REQUIRED_CLEANUP_STEPS = (
    "live_host_stopped",
    "provider_session_removed",
    "workspace_published",
    _PROVIDER_PROFILE_RELEASE_STEP,
)
_DRAIN_CLEANUP_STEPS = (
    "live_host_drained",
    "provider_session_removed",
    "workspace_published",
    _PROVIDER_PROFILE_RELEASE_STEP,
)
_CLEANUP_STEPS_BY_MODE: Mapping[str, tuple[str, ...]] = {
    "remove": _REQUIRED_CLEANUP_STEPS,
    "drain": _DRAIN_CLEANUP_STEPS,
}
_CLEANUP_STEP_KINDS = frozenset(_REQUIRED_CLEANUP_STEPS) | frozenset(
    _DRAIN_CLEANUP_STEPS
)
# Cross-scope denials only prove isolation when they record the authorized and
# attempted identities, so a relabelled ordinary 403 cannot satisfy them.
_CROSS_SCOPE_IDENTITY_FIELDS = (
    "authorizedUserId",
    "attemptedUserId",
    "authorizedWorkflowId",
    "attemptedWorkflowId",
)
_CROSS_SCOPE_DENIAL_KINDS = frozenset({"cross_user", "cross_workflow"})
_EXECUTIONS_CREATE_PATH = "/api/executions"
_TERMINAL_STATES = frozenset(
    {"completed", "failed", "canceled", "cancelled", "timed_out", "stopped"}
)


def _require_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConformanceContractError(
            f"workflow Chat source record {field} must be an object"
        )
    return value


def _require_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConformanceContractError(
            f"workflow Chat source record {field} must be a non-empty string"
        )
    return value


def _require_string_list(value: Any, *, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ConformanceContractError(
            f"workflow Chat source record {field} must contain unique strings"
        )
    return value


def _require_sha256_ref(value: Any, *, field: str) -> str:
    result = _require_string(value, field=field)
    if _SHA256_REF.fullmatch(result) is None:
        raise ConformanceContractError(
            f"workflow Chat source record {field} must be an immutable digest"
        )
    return result


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _is_scoped_path(
    path: str,
    allowed_bases: tuple[str, ...],
    *,
    allowed_exact: tuple[str, ...] = (),
) -> bool:
    parsed_path = urllib.parse.urlsplit(path).path
    if parsed_path in allowed_exact:
        return True
    return any(
        parsed_path == base or parsed_path.startswith(base + "/")
        for base in allowed_bases
    )


def _require_evidence_items(value: Any, *, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ConformanceContractError(
            f"workflow Chat source record {field} must contain evidence items"
        )
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_item in value:
        item = _require_mapping(raw_item, field=f"{field}[]")
        ref = _require_string(item.get("ref"), field=f"{field}[].ref")
        parsed = urllib.parse.urlparse(ref)
        if (
            ref in seen
            or parsed.scheme not in {"", "https"}
            or (not parsed.scheme and Path(ref).is_absolute())
        ):
            raise ConformanceContractError(
                f"workflow Chat source record {field} contains an unpackaged ref"
            )
        digest = _require_string(item.get("sha256"), field=f"{field}[].sha256")
        if _SHA256.fullmatch(digest) is None:
            raise ConformanceContractError(
                f"workflow Chat source record {field} contains a malformed digest"
            )
        seen.add(ref)
        result.append({"ref": ref, "sha256": digest})
    return result


def _validate_cross_scope_denial(
    denial: Mapping[str, Any], *, kind: str, workflow_id: str
) -> None:
    """Require the identities that make a cross-scope denial meaningful.

    ``cross_user`` and ``cross_workflow`` are isolation claims, not labels: the
    audit must name the authorized scope under test and the different scope the
    attempt used, so an ordinary 403 cannot be relabelled as either check.
    """

    identity = _require_mapping(
        denial.get("scopeIdentity"),
        field="denialAudit.data.denials[].scopeIdentity",
    )
    if set(identity) != set(_CROSS_SCOPE_IDENTITY_FIELDS):
        raise ConformanceContractError(
            f"workflow Chat {kind} denial does not record both scope identities"
        )
    values = {
        field: _require_string(
            identity[field],
            field=f"denialAudit.data.denials[].scopeIdentity.{field}",
        )
        for field in _CROSS_SCOPE_IDENTITY_FIELDS
    }
    if values["authorizedWorkflowId"] != workflow_id:
        raise ConformanceContractError(
            f"workflow Chat {kind} denial is not bound to the workflow under test"
        )
    same_user = values["attemptedUserId"] == values["authorizedUserId"]
    same_workflow = values["attemptedWorkflowId"] == values["authorizedWorkflowId"]
    # Exactly one scope may vary, otherwise the denial does not isolate the
    # property it claims to prove.
    if (kind == "cross_user" and (same_user or not same_workflow)) or (
        kind == "cross_workflow" and (same_workflow or not same_user)
    ):
        raise ConformanceContractError(
            f"workflow Chat {kind} denial does not vary exactly that scope"
        )


def _validate_record_data(
    record_type: str,
    data: Mapping[str, Any],
    *,
    correlation: Mapping[str, str],
    combination: WorkflowChatCombination,
) -> None:
    request_ids = _require_string_list(
        data.get("requestIds"), field=f"{record_type}.data.requestIds"
    )
    request_id_set = set(request_ids)
    workflow_id = correlation["workflowId"]
    binding_id = correlation["chatBindingId"]
    provider_session_id = correlation["providerSessionId"]

    if record_type == "browserTrace":
        events = data.get("networkEvents")
        if not isinstance(events, list) or not events:
            raise ConformanceContractError(
                "workflow Chat browserTrace requires observed network events"
            )
        event_ids: set[str] = set()
        allowed_prefixes = (
            f"/omnigent-ui/workflow-chat/{binding_id}",
            f"/api/workflow-chat-bindings/{binding_id}/omnigent",
            f"/api/executions/{workflow_id}",
        )
        for event in events:
            item = _require_mapping(event, field="browserTrace.networkEvents[]")
            request_id = _require_string(
                item.get("requestId"), field="browserTrace.networkEvents[].requestId"
            )
            transport = _require_string(
                item.get("transport"), field="browserTrace.networkEvents[].transport"
            )
            path = _require_string(
                item.get("path"), field="browserTrace.networkEvents[].path"
            )
            _require_string(
                item.get("method"), field="browserTrace.networkEvents[].method"
            )
            status = item.get("responseStatus")
            if (
                request_id in event_ids
                or transport not in _TRANSPORTS
                or not _is_scoped_path(
                    path,
                    allowed_prefixes,
                    allowed_exact=(_EXECUTIONS_CREATE_PATH,),
                )
                or not isinstance(status, int)
                or not 100 <= status <= 599
                or item.get("moonmindScoped") is not True
                or item.get("browserOriginated") is not True
                or "providerSessionId" in item
                or "upstreamUrl" in item
            ):
                raise ConformanceContractError(
                    "workflow Chat browserTrace contains an unscoped or "
                    "malformed request"
                )
            event_ids.add(request_id)
        if (
            event_ids != request_id_set
            or data.get("route") != f"/workflows/{workflow_id}/chat"
            or data.get("traceId") != correlation["browserTraceId"]
            or data.get("directUpstreamRequestCount") != 0
            or data.get("exposedProviderFields") != []
        ):
            raise ConformanceContractError(
                "workflow Chat browserTrace does not prove the scoped product route"
            )
        _require_sha256_ref(
            data.get("screenshotSha256"), field="browserTrace.data.screenshotSha256"
        )
        return

    if record_type == "executionCreation":
        create_request_id = data.get("createRequestId")
        if (
            create_request_id not in request_id_set
            or data.get("method") != "POST"
            or urllib.parse.urlsplit(str(data.get("path") or "")).path
            != _EXECUTIONS_CREATE_PATH
            or data.get("createdThroughPublicApi") is not True
            or data.get("workflowId") != workflow_id
            or data.get("resolvedBridgeSessionId") != correlation["bridgeSessionId"]
            or data.get("harnessId") != combination.harness_id
            or data.get("executionRealizerRef") != combination.execution_realizer_ref
            or data.get("launchPolicyRef") != combination.launch_policy_ref
        ):
            raise ConformanceContractError(
                "workflow Chat executionCreation does not prove the normal "
                "/api/executions create path"
            )
        for field in (
            "temporalWorkflowId",
            "temporalRunId",
            "temporalTaskQueue",
            "agentProfileSnapshotRef",
            "executionPlanRef",
            "providerProfileRef",
        ):
            _require_string(data.get(field), field=f"executionCreation.data.{field}")
        return

    if record_type == "bindingSnapshot":
        if (
            data.get("authoritative") is not True
            or data.get("resolvedBindingId") != binding_id
            or data.get("state") not in {"starting", "available", "ended"}
            or not isinstance(data.get("readOnly"), bool)
        ):
            raise ConformanceContractError(
                "workflow Chat bindingSnapshot is not authoritative"
            )
        _require_string(data.get("runId"), field="bindingSnapshot.data.runId")
        _require_sha256_ref(
            data.get("capabilitiesDigest"),
            field="bindingSnapshot.data.capabilitiesDigest",
        )
        return

    if record_type == "nativeConversation":
        if (
            data.get("renderer") != "omnigent-native"
            or data.get("composerRequestId") not in request_id_set
        ):
            raise ConformanceContractError(
                "workflow Chat nativeConversation is not the native send path"
            )
        _require_string_list(
            data.get("transcriptMessageIds"),
            field="nativeConversation.data.transcriptMessageIds",
        )
        _require_string_list(
            data.get("queuedMessageIds"),
            field="nativeConversation.data.queuedMessageIds",
        )
        _require_string(
            data.get("nativeAppVersion"),
            field="nativeConversation.data.nativeAppVersion",
        )
        return

    if record_type == "nativeControls":
        controls = set(
            _require_string_list(
                data.get("controlKinds"), field="nativeControls.data.controlKinds"
            )
        )
        if (
            not _NATIVE_CONTROL_KINDS.issubset(controls)
            or data.get("renderer") != "omnigent-native"
            or data.get("customComposerCount") != 0
        ):
            raise ConformanceContractError(
                "workflow Chat nativeControls do not prove native-primary controls"
            )
        return

    if record_type == "facadeRequests":
        requests = data.get("requests")
        if not isinstance(requests, list) or not requests:
            raise ConformanceContractError(
                "workflow Chat facadeRequests requires resolved requests"
            )
        observed_ids: set[str] = set()
        transports: set[str] = set()
        observed_route_names: set[str] = set()
        reconnect_id = data.get("reconnectRequestId")
        reconnect_reauthorized = False
        routes = compatibility_map()["routes"]
        expected_routes = {str(item["name"]): item for item in routes}
        for request in requests:
            item = _require_mapping(request, field="facadeRequests.data.requests[]")
            request_id = _require_string(
                item.get("requestId"), field="facadeRequests.data.requests[].requestId"
            )
            transport = _require_string(
                item.get("transport"), field="facadeRequests.data.requests[].transport"
            )
            route_name = _require_string(
                item.get("routeName"), field="facadeRequests.data.requests[].routeName"
            )
            method = _require_string(
                item.get("method"), field="facadeRequests.data.requests[].method"
            )
            route_path = _require_string(
                item.get("routePath"), field="facadeRequests.data.requests[].routePath"
            )
            route = expected_routes.get(route_name)
            route_matches = (
                transport == "html"
                and route_name == _NATIVE_UI_DOCUMENT_ROUTE
                and method == "GET"
                and route_path == f"omnigent-ui/workflow-chat/{binding_id}"
            ) or (
                route is not None
                and transport == route.get("transport")
                and method in route.get("methods", [])
                and re.fullmatch(str(route.get("pathPattern") or ""), route_path)
                is not None
            )
            if (
                request_id in observed_ids
                or request_id not in request_id_set
                or transport not in _TRANSPORTS
                or not route_matches
                or item.get("bindingId") != binding_id
                or item.get("providerSessionId") != provider_session_id
                or item.get("authorized") is not True
                or item.get("serverResolvedTarget") is not True
            ):
                raise ConformanceContractError(
                    "workflow Chat facadeRequests contains an unauthorized request"
                )
            observed_ids.add(request_id)
            transports.add(transport)
            observed_route_names.add(route_name)
            if request_id == reconnect_id:
                reconnect_reauthorized = item.get("reauthorized") is True
        if (
            observed_ids != request_id_set
            or transports != set(_TRANSPORTS)
            or observed_route_names
            != set(expected_routes) | {_NATIVE_UI_DOCUMENT_ROUTE}
            or data.get("compatibilityProfile") != WORKFLOW_CHAT_COMPATIBILITY_PROFILE
            or reconnect_id not in request_id_set
            or not reconnect_reauthorized
        ):
            raise ConformanceContractError(
                "workflow Chat facadeRequests coverage or reconnect evidence is "
                "incomplete"
            )
        return

    if record_type == "resourceInventory":
        resources = data.get("resources")
        if not isinstance(resources, list) or not resources:
            raise ConformanceContractError(
                "workflow Chat resourceInventory requires observed resources"
            )
        resource_types: set[str] = set()
        for resource in resources:
            item = _require_mapping(
                resource, field="resourceInventory.data.resources[]"
            )
            _require_string(
                item.get("resourceId"),
                field="resourceInventory.data.resources[].resourceId",
            )
            resource_types.add(
                _require_string(
                    item.get("resourceType"),
                    field="resourceInventory.data.resources[].resourceType",
                )
            )
            if item.get("requestId") not in request_id_set:
                raise ConformanceContractError(
                    "workflow Chat resourceInventory is not request-correlated"
                )
        if not _NATIVE_CONTROL_KINDS.issubset(resource_types):
            raise ConformanceContractError(
                "workflow Chat resourceInventory is incomplete"
            )
        return

    if record_type == "mutationReceipts":
        receipts = data.get("receipts")
        if not isinstance(receipts, list) or not receipts:
            raise ConformanceContractError(
                "workflow Chat mutationReceipts requires durable receipts"
            )
        receipt_ids: set[str] = set()
        for receipt in receipts:
            item = _require_mapping(receipt, field="mutationReceipts.data.receipts[]")
            request_id = item.get("requestId")
            if request_id not in request_id_set or request_id in receipt_ids:
                raise ConformanceContractError(
                    "workflow Chat mutation receipt is not request-correlated"
                )
            receipt_ids.add(str(request_id))
            for field in (
                "actor",
                "idempotencyKey",
                "expectedState",
                "outcome",
                "upstreamCorrelation",
                "auditRef",
            ):
                _require_string(
                    item.get(field),
                    field=f"mutationReceipts.data.receipts[].{field}",
                )
        if receipt_ids != request_id_set:
            raise ConformanceContractError(
                "workflow Chat mutation receipt coverage is incomplete"
            )
        return

    if record_type == "denialAudit":
        denials = data.get("denials")
        if not isinstance(denials, list) or not denials:
            raise ConformanceContractError(
                "workflow Chat denialAudit requires observed denials"
            )
        kinds: set[str] = set()
        denial_ids: set[str] = set()
        for denial in denials:
            item = _require_mapping(denial, field="denialAudit.data.denials[]")
            kind = _require_string(
                item.get("kind"), field="denialAudit.data.denials[].kind"
            )
            kinds.add(kind)
            if (
                item.get("requestId") not in request_id_set
                or item.get("requestId") in denial_ids
                or item.get("upstreamForwarded") is not False
            ):
                raise ConformanceContractError(
                    "workflow Chat denialAudit is not fail-closed"
                )
            denial_ids.add(str(item["requestId"]))
            _require_string(
                item.get("auditRef"), field="denialAudit.data.denials[].auditRef"
            )
            if kind in _CROSS_SCOPE_DENIAL_KINDS:
                _validate_cross_scope_denial(item, kind=kind, workflow_id=workflow_id)
        if not _DENIAL_KINDS.issubset(kinds):
            raise ConformanceContractError(
                "workflow Chat denialAudit coverage is incomplete"
            )
        return

    if record_type == "capabilitySnapshot":
        inputs = _require_mapping(
            data.get("inputs"), field="capabilitySnapshot.data.inputs"
        )
        expected_inputs = (
            "upstream",
            "agentProfile",
            "providerPolicy",
            "workflowState",
            "callerPermission",
        )
        if set(inputs) != set(expected_inputs):
            raise ConformanceContractError(
                "workflow Chat capabilitySnapshot inputs are incomplete"
            )
        typed_inputs: list[Mapping[str, Any]] = []
        for name in expected_inputs:
            values = _require_mapping(
                inputs[name], field=f"capabilitySnapshot.data.inputs.{name}"
            )
            if not values or any(
                not isinstance(value, bool) for value in values.values()
            ):
                raise ConformanceContractError(
                    "workflow Chat capabilitySnapshot inputs must be boolean maps"
                )
            typed_inputs.append(values)
        effective = _require_mapping(
            data.get("effective"), field="capabilitySnapshot.data.effective"
        )
        capability_names = set().union(*(set(value) for value in typed_inputs))
        computed = {
            name: all(values.get(name) is True for values in typed_inputs)
            for name in capability_names
        }
        advertised = combination.advertised_capabilities
        declared = _require_string_list(
            data.get("advertised"), field="capabilitySnapshot.data.advertised"
        )
        digest_payload = {
            "inputs": inputs,
            "effective": computed,
            "advertised": sorted(advertised),
        }
        if (
            dict(effective) != computed
            or set(declared) != set(advertised)
            or set(computed) != set(advertised)
            or data.get("snapshotDigest") != _canonical_digest(digest_payload)
        ):
            raise ConformanceContractError(
                "workflow Chat capabilitySnapshot is not the effective "
                "intersection of the advertised capability contract"
            )
        enforcement = _require_mapping(
            data.get("enforcement"), field="capabilitySnapshot.data.enforcement"
        )
        if set(enforcement) != set(advertised):
            raise ConformanceContractError(
                "workflow Chat capabilitySnapshot does not cover every "
                "advertised capability"
            )
        for name in sorted(advertised):
            observation = _require_mapping(
                enforcement[name],
                field=f"capabilitySnapshot.data.enforcement.{name}",
            )
            expected_outcome = "allowed" if computed[name] else "denied"
            if (
                observation.get("requestId") not in request_id_set
                or observation.get("outcome") != expected_outcome
            ):
                raise ConformanceContractError(
                    "workflow Chat capability enforcement does not match the "
                    f"effective decision: {name}"
                )
        return

    if record_type == "scanAudit":
        attempts = data.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ConformanceContractError(
                "workflow Chat scanAudit requires observed scan attempts"
            )
        outcomes: set[str] = set()
        attempt_ids: set[str] = set()
        for attempt in attempts:
            item = _require_mapping(attempt, field="scanAudit.data.attempts[]")
            outcomes.add(
                _require_string(
                    item.get("outcome"), field="scanAudit.data.attempts[].outcome"
                )
            )
            if (
                item.get("requestId") not in request_id_set
                or item.get("requestId") in attempt_ids
                or item.get("forwarded") is not False
            ):
                raise ConformanceContractError(
                    "workflow Chat scanAudit did not fail closed"
                )
            attempt_ids.add(str(item["requestId"]))
            _require_string(
                item.get("auditRef"), field="scanAudit.data.attempts[].auditRef"
            )
        if not {"blocked", "enforcement_unavailable"}.issubset(outcomes):
            raise ConformanceContractError(
                "workflow Chat scanAudit coverage is incomplete"
            )
        return

    if record_type == "credentialBoundary":
        verified = set(
            _require_string_list(
                data.get("verifiedRequestIds"),
                field="credentialBoundary.data.verifiedRequestIds",
            )
        )
        if (
            verified != request_id_set
            or data.get("browserExposedCredentialNames") != []
            or data.get("forwardedMoonMindCredentialNames") != []
        ):
            raise ConformanceContractError(
                "workflow Chat credentialBoundary detected credential crossover"
            )
        _require_string(
            data.get("serverInjectedCredentialRef"),
            field="credentialBoundary.data.serverInjectedCredentialRef",
        )
        return

    if record_type == "terminalSnapshot":
        denied_ids = set(
            _require_string_list(
                data.get("deniedMutationRequestIds"),
                field="terminalSnapshot.data.deniedMutationRequestIds",
            )
        )
        if (
            data.get("state") not in _TERMINAL_STATES
            or data.get("readOnly") is not True
            or not denied_ids.issubset(request_id_set)
        ):
            raise ConformanceContractError(
                "workflow Chat terminalSnapshot is not read-only"
            )
        return

    if record_type == "capturedEvidence":
        artifacts = _require_evidence_items(
            data.get("artifacts"), field="capturedEvidence.data.artifacts"
        )
        refs = {item["ref"] for item in artifacts}
        if data.get("captureManifestRef") not in refs:
            raise ConformanceContractError(
                "workflow Chat capturedEvidence is not independently resolvable"
            )
        return

    if record_type == "continuationReceipt":
        creation = _require_mapping(
            data.get("destinationCreationReceipt"),
            field="continuationReceipt.data.destinationCreationReceipt",
        )
        relationship = _require_mapping(
            data.get("durableRelationship"),
            field="continuationReceipt.data.durableRelationship",
        )
        source_run_id = _require_string(
            creation.get("sourceRunId"),
            field="continuationReceipt.data.destinationCreationReceipt.sourceRunId",
        )
        destination = _require_string(
            creation.get("destinationWorkflowId"),
            field=(
                "continuationReceipt.data.destinationCreationReceipt."
                "destinationWorkflowId"
            ),
        )
        destination_run_id = _require_string(
            relationship.get("destinationRunId"),
            field="continuationReceipt.data.durableRelationship.destinationRunId",
        )
        relationship_identity = {
            "relationshipType": _LINKED_CONTINUATION_RELATIONSHIP,
            "sourceWorkflowId": workflow_id,
            "sourceRunId": source_run_id,
            "destinationWorkflowId": destination,
            "destinationRunId": destination_run_id,
        }
        if (
            creation.get("requestId") not in request_id_set
            or creation.get("created") is not True
            or creation.get("relationshipType") != _LINKED_CONTINUATION_RELATIONSHIP
            or creation.get("sourceWorkflowId") != workflow_id
            or destination == workflow_id
            or relationship.get("requestId") not in request_id_set
            or relationship.get("requestId") == creation.get("requestId")
            or relationship.get("direction") != "outbound"
            or any(
                relationship.get(field) != value
                for field, value in relationship_identity.items()
            )
            or relationship.get("relationshipDigest")
            != _canonical_digest(relationship_identity)
            or data.get("sourceStateBeforeSha256") != data.get("sourceStateAfterSha256")
        ):
            raise ConformanceContractError(
                "workflow Chat continuationReceipt does not prove linked continuation"
            )
        _require_sha256_ref(
            data.get("sourceStateBeforeSha256"),
            field="continuationReceipt.data.sourceStateBeforeSha256",
        )
        _require_string(
            data.get("idempotencyKey"), field="continuationReceipt.data.idempotencyKey"
        )
        _timestamp(
            relationship.get("createdAt"),
            field="continuationReceipt.data.durableRelationship.createdAt",
        )
        return

    if record_type == "cleanupReceipt":
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ConformanceContractError(
                "workflow Chat cleanupReceipt requires ordered cleanup steps"
            )
        required_steps = combination.required_cleanup_steps
        observed: dict[int, str] = {}
        for raw_step in steps:
            step = _require_mapping(raw_step, field="cleanupReceipt.data.steps[]")
            kind = _require_string(
                step.get("kind"), field="cleanupReceipt.data.steps[].kind"
            )
            order = step.get("order")
            if (
                kind not in _CLEANUP_STEP_KINDS
                or not isinstance(order, int)
                or isinstance(order, bool)
                or order in observed
                or step.get("outcome") != "completed"
            ):
                raise ConformanceContractError(
                    "workflow Chat cleanupReceipt step is malformed or incomplete"
                )
            _require_string(
                step.get("auditRef"), field="cleanupReceipt.data.steps[].auditRef"
            )
            observed[order] = kind
        ordered = [observed[key] for key in sorted(observed)]
        if (
            sorted(observed) != list(range(1, len(ordered) + 1))
            # The documented contract is an order, not a set: publishing the
            # workspace before removing the provider session is not equivalent.
            or tuple(ordered) != required_steps
            or data.get("liveResourcesRemoved")
            is not combination.live_resources_removed_expected
            or data.get("providerProfileReleasedLast") is not True
            or data.get("outcome") != "released"
            or data.get("hostMode") != combination.host_mode
            or data.get("cleanupMode") != combination.cleanup_mode
        ):
            raise ConformanceContractError(
                "workflow Chat cleanupReceipt does not prove release-last cleanup"
            )
        _require_string(
            data.get("cleanupState"), field="cleanupReceipt.data.cleanupState"
        )
        return

    if record_type == "replaySnapshot":
        replay_refs = _require_string_list(
            data.get("artifactRefs"), field="replaySnapshot.data.artifactRefs"
        )
        if (
            data.get("hostUnavailable") is not True
            or data.get("replayedFromMoonMindArtifacts") is not True
            or any(
                urllib.parse.urlparse(ref).scheme not in {"", "https"}
                or (not urllib.parse.urlparse(ref).scheme and Path(ref).is_absolute())
                for ref in replay_refs
            )
        ):
            raise ConformanceContractError(
                "workflow Chat replaySnapshot does not prove host-independent replay"
            )
        return


def validate_workflow_chat_source_records(
    sources: Mapping[str, Mapping[str, Any]],
    *,
    row_name: str,
    source_commit: str,
    images: Mapping[str, Any],
    generated_at: datetime,
    combination: WorkflowChatCombination,
    expected_correlation: Mapping[str, str] | None = None,
) -> tuple[dict[str, bool], dict[str, str]]:
    """Validate and correlate one row's production-owned source records.

    The returned assertions are derived from typed observations. Callers never
    trust an adapter-authored pass boolean as the semantic result.
    """

    required = REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS.get(row_name)
    if required is None or set(sources) != set(required):
        raise ConformanceContractError(
            f"workflow Chat source record coverage is incomplete: {row_name}"
        )
    row_correlation: dict[str, str] | None = None
    request_ids_by_type: dict[str, set[str]] = {}
    for record_type, source in sources.items():
        if (
            source.get("schemaVersion") != WORKFLOW_CHAT_SOURCE_RECORD_VERSION
            or source.get("recordType") != record_type
            or source.get("row") != row_name
            or source.get("sourceCommit") != source_commit
            or source.get("images") != images
            or source.get("observed") is not True
        ):
            raise ConformanceContractError(
                f"workflow Chat source record is invalid: {row_name}/{record_type}"
            )
        observed_at = _timestamp(
            source.get("observedAt"), field=f"{record_type}.observedAt"
        )
        if observed_at > generated_at or generated_at - observed_at > timedelta(days=1):
            raise ConformanceContractError(
                f"workflow Chat source record is stale: {row_name}/{record_type}"
            )
        raw_correlation = _require_mapping(
            source.get("correlation"), field=f"{record_type}.correlation"
        )
        if set(raw_correlation) != set(_CORRELATION_FIELDS):
            raise ConformanceContractError(
                f"workflow Chat source record correlation is incomplete: {record_type}"
            )
        correlation = {
            field: _require_string(
                raw_correlation[field], field=f"{record_type}.correlation.{field}"
            )
            for field in _CORRELATION_FIELDS
        }
        if row_correlation is None:
            row_correlation = correlation
        elif correlation != row_correlation:
            raise ConformanceContractError(
                f"workflow Chat source records are not correlated: {row_name}"
            )
        if source.get("combination") != combination.combination_id:
            raise ConformanceContractError(
                "workflow Chat source record is not bound to the combination "
                f"under test: {row_name}/{record_type}"
            )
        data = _require_mapping(source.get("data"), field=f"{record_type}.data")
        _validate_record_data(
            record_type, data, correlation=correlation, combination=combination
        )
        request_ids_by_type[record_type] = set(data["requestIds"])

    assert row_correlation is not None
    if expected_correlation is not None and any(
        row_correlation[field] != expected_correlation.get(field)
        for field in _GLOBAL_CORRELATION_FIELDS
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance rows do not bind the same workflow session"
        )
    browser_request_ids = request_ids_by_type["browserTrace"]
    for record_type, request_ids in request_ids_by_type.items():
        if record_type != "browserTrace" and not request_ids.issubset(
            browser_request_ids
        ):
            raise ConformanceContractError(
                f"workflow Chat source record request correlation failed: {record_type}"
            )
    browser_events = {
        str(item["requestId"]): item
        for item in sources["browserTrace"]["data"]["networkEvents"]
    }
    creation_source = sources.get("executionCreation")
    if creation_source is not None:
        creation_data = creation_source["data"]
        create_event = browser_events[str(creation_data["createRequestId"])]
        # The record must describe the same observed call the browser made, not
        # merely reuse a request ID seen somewhere in the trace.
        if (
            str(create_event["method"]) != "POST"
            or urllib.parse.urlsplit(str(create_event["path"])).path
            != _EXECUTIONS_CREATE_PATH
        ):
            raise ConformanceContractError(
                "workflow Chat executionCreation does not match the observed "
                "browser create request"
            )
    expected_denial_statuses: dict[str, int] = {}
    capability_source = sources.get("capabilitySnapshot")
    if capability_source is not None:
        # A capability that evaluates false is enforced with a real denial, so
        # its browser event is a 403 rather than an unsuccessful positive.
        expected_denial_statuses.update(
            {
                str(observation["requestId"]): 403
                for observation in capability_source["data"]["enforcement"].values()
                if observation.get("outcome") == "denied"
            }
        )
    denial_source = sources.get("denialAudit")
    if denial_source is not None:
        expected_denial_statuses.update(
            {str(item["requestId"]): 403 for item in denial_source["data"]["denials"]}
        )
    scan_source = sources.get("scanAudit")
    if scan_source is not None:
        expected_denial_statuses.update(
            {
                str(item["requestId"]): (
                    503 if item["outcome"] == "enforcement_unavailable" else 403
                )
                for item in scan_source["data"]["attempts"]
            }
        )
    terminal_source = sources.get("terminalSnapshot")
    if terminal_source is not None:
        expected_denial_statuses.update(
            {
                str(request_id): 403
                for request_id in terminal_source["data"]["deniedMutationRequestIds"]
            }
        )
    for request_id, event in browser_events.items():
        status = event["responseStatus"]
        expected_denial = expected_denial_statuses.get(request_id)
        unexpected_denial = expected_denial is not None and status != expected_denial
        unsuccessful_positive = (
            expected_denial is None and status != 101 and not 200 <= status < 300
        )
        if unexpected_denial or unsuccessful_positive:
            raise ConformanceContractError(
                "workflow Chat browserTrace contains an unexpected response status"
            )

    facade_source = sources.get("facadeRequests")
    mutation_source = sources.get("mutationReceipts")
    if facade_source is not None and mutation_source is not None:
        route_mutations = {
            str(item["name"]): item.get("mutation") is True
            for item in compatibility_map()["routes"]
        }
        facade_requests = facade_source["data"]["requests"]
        observed_mutation_ids = {
            str(item["requestId"])
            for item in facade_requests
            if route_mutations.get(str(item["routeName"]), False)
        }
        receipt_ids = {
            str(item["requestId"]) for item in mutation_source["data"]["receipts"]
        }

        def _request_matches_browser_trace(item: Mapping[str, Any]) -> bool:
            request_id = str(item["requestId"])
            event = browser_events[request_id]
            observed_path = urllib.parse.urlsplit(str(event["path"])).path
            expected_path = (
                "/" + str(item["routePath"])
                if item["routeName"] == _NATIVE_UI_DOCUMENT_ROUTE
                else f"/api/workflow-chat-bindings/{row_correlation['chatBindingId']}"
                f"/omnigent/{item['routePath']}"
            )
            return (
                event["transport"] == item["transport"]
                and event["method"] == item["method"]
                and observed_path == expected_path
            )

        if (
            observed_mutation_ids != receipt_ids
            or set(mutation_source["data"]["requestIds"]) != observed_mutation_ids
            or any(not _request_matches_browser_trace(item) for item in facade_requests)
        ):
            raise ConformanceContractError(
                "workflow Chat mutation receipt or transport coverage is incomplete"
            )

    credential_source = sources.get("credentialBoundary")
    if credential_source is not None and (
        set(credential_source["data"]["verifiedRequestIds"]) != browser_request_ids
        or request_ids_by_type["credentialBoundary"] != browser_request_ids
    ):
        raise ConformanceContractError(
            "workflow Chat credentialBoundary does not cover the browser trace"
        )
    if row_name == "terminal-evidence-and-continuation":
        captured_refs = {
            str(item["ref"])
            for item in sources["capturedEvidence"]["data"]["artifacts"]
        }
        replay_refs = set(sources["replaySnapshot"]["data"]["artifactRefs"])
        if not replay_refs.issubset(captured_refs):
            raise ConformanceContractError(
                "workflow Chat replay evidence is not bound to captured evidence"
            )

    assertions = {name: True for name in REQUIRED_WORKFLOW_CHAT_ROWS[row_name]}
    return assertions, row_correlation


def _timestamp(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ConformanceContractError(
            f"workflow Chat acceptance {field} is missing or malformed"
        ) from exc
    if parsed.tzinfo is None:
        raise ConformanceContractError(
            f"workflow Chat acceptance {field} requires a timezone"
        )
    return parsed


def _resolve_bytes(ref: str, evidence_root: Path) -> bytes:
    parsed = urllib.parse.urlparse(str(ref))
    try:
        if parsed.scheme == "https":
            with urllib.request.urlopen(ref, timeout=15) as response:
                return response.read()
        if parsed.scheme not in {"", "file"}:
            raise ConformanceContractError(
                f"unsupported workflow Chat evidence scheme: {parsed.scheme}"
            )
        candidate = Path(urllib.request.url2pathname(parsed.path))
        if not candidate.is_absolute():
            candidate = evidence_root / candidate
        candidate = candidate.resolve()
        root = evidence_root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ConformanceContractError(
                "workflow Chat evidence path escapes its run artifact"
            )
        return candidate.read_bytes()
    except (OSError, urllib.error.URLError) as exc:
        raise ConformanceContractError(
            f"workflow Chat evidence is unresolved: {ref}"
        ) from exc


def _resolve_json(ref: str, evidence_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_resolve_bytes(ref, evidence_root))
    except json.JSONDecodeError as exc:
        raise ConformanceContractError(
            f"workflow Chat evidence is malformed: {ref}"
        ) from exc
    if not isinstance(value, dict):
        raise ConformanceContractError(
            f"workflow Chat evidence must be an object: {ref}"
        )
    return value


_BINDING_IDENTITY_FIELDS = (
    "omnigentServerBuildRef",
    "omnigentHostBuildRef",
    "harnessImplementationRef",
    "vendorRuntimeRefs",
    "agentSourceRef",
    "materializerRefs",
    "providerCompatibilityClass",
    "hostClassRef",
    "architecture",
    "launchPolicyRef",
    "modelConfigDigest",
    "executionRealizerRef",
    "requiredCapabilitiesDigest",
)
_EXECUTION_SUPPORT_IDENTITY_FIELDS = (
    "policySnapshotDigest",
    "effectiveLaunchSnapshotDigest",
)


def _validate_binding_identity(
    identity: Any, *, combination: WorkflowChatCombination
) -> None:
    """Bind a combination's evidence to the exact support-combination identity.

    The report is only usable as release evidence when it names the harness
    implementation, vendor runtime, realizer, launch policy, normalized model
    configuration, and Provider Profile class that actually ran, and when the
    recomputed support-combination key agrees with the recorded one.
    """

    payload = _require_mapping(
        identity, field=f"combinations.{combination.combination_id}.bindingIdentity"
    )
    expected_keys = set(_BINDING_IDENTITY_FIELDS) | set(
        _EXECUTION_SUPPORT_IDENTITY_FIELDS
    ) | {
        "supportCombinationKey",
        "providerProfileClass",
    }
    if set(payload) != expected_keys:
        raise ConformanceContractError(
            "workflow Chat acceptance binding identity is incomplete: "
            f"{combination.combination_id}"
        )
    materializer_refs = _require_string_list(
        payload.get("materializerRefs"), field="bindingIdentity.materializerRefs"
    )
    if (
        payload.get("hostClassRef") != combination.host_class_ref
        or payload.get("launchPolicyRef") != combination.launch_policy_ref
        or payload.get("executionRealizerRef") != combination.execution_realizer_ref
        # The Provider Profile class is not part of the canonical support-key
        # payload, so it is pinned by the claimed inventory instead: evidence
        # collected under one credential authority class cannot qualify another.
        or payload.get("providerProfileClass") != combination.provider_profile_class
        or combination.credential_materializer_ref not in materializer_refs
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance binding identity does not match the "
            f"combination under test: {combination.combination_id}"
        )
    try:
        support_payload = SupportKeyPayload.model_validate(
            {field: payload[field] for field in _BINDING_IDENTITY_FIELDS}
        )
    except ValueError as exc:
        raise ConformanceContractError(
            "workflow Chat acceptance binding identity is not a valid support "
            f"combination: {combination.combination_id}"
        ) from exc
    if payload.get("supportCombinationKey") != compute_support_combination_key(
        support_payload
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance support combination key does not recompute: "
            f"{combination.combination_id}"
        )
    for field in _EXECUTION_SUPPORT_IDENTITY_FIELDS:
        _require_sha256_ref(payload.get(field), field=f"bindingIdentity.{field}")


def build_workflow_chat_acceptance_manifest(
    source: Mapping[str, Any],
    *,
    evidence_root: Path,
) -> dict[str, Any]:
    """Bind a protected-run combination matrix to immutable evidence digests.

    ``source`` is the run-local matrix produced by the protected controller. It
    carries combinations, rows, and refs, never raw credentials.  This function
    resolves every referenced file before emitting a candidate manifest;
    validation remains a separate mandatory step.
    """

    combinations = source.get("combinations")
    scans = source.get("evidenceScans")
    if not isinstance(combinations, Mapping) or not isinstance(scans, Mapping):
        raise ConformanceContractError(
            "workflow Chat acceptance source lacks combinations or evidence scans"
        )
    refs: list[str] = []
    case_refs: list[str] = []
    for combination in combinations.values():
        if not isinstance(combination, Mapping):
            raise ConformanceContractError(
                "workflow Chat acceptance combination must be an object"
            )
        rows = combination.get("rows")
        if isinstance(rows, Mapping):
            for row in rows.values():
                if isinstance(row, Mapping) and isinstance(
                    row.get("evidenceRefs"), list
                ):
                    row_refs = [str(ref) for ref in row["evidenceRefs"]]
                    refs.extend(row_refs)
                    case_refs.extend(row_refs)
        if isinstance(combination.get("reports"), list):
            refs.extend(str(ref) for ref in combination["reports"])
        if combination.get("timelineRef"):
            refs.append(str(combination["timelineRef"]))
    scan_refs: list[str] = []
    for channel in REQUIRED_EVIDENCE_CHANNELS:
        scan = scans.get(channel)
        if isinstance(scan, Mapping) and scan.get("evidenceRef"):
            scan_ref = str(scan["evidenceRef"])
            refs.append(scan_ref)
            scan_refs.append(scan_ref)
    for case_ref in dict.fromkeys(case_refs):
        case = _resolve_json(case_ref, evidence_root)
        source_records = case.get("sourceRecords")
        if isinstance(source_records, list):
            for record in source_records:
                if isinstance(record, Mapping) and record.get("ref"):
                    record_ref = str(record["ref"])
                    refs.append(record_ref)
                    source_record = _resolve_json(record_ref, evidence_root)
                    if source_record.get("recordType") == "capturedEvidence":
                        data = source_record.get("data")
                        artifacts = (
                            data.get("artifacts") if isinstance(data, Mapping) else None
                        )
                        if isinstance(artifacts, list):
                            for artifact in artifacts:
                                if isinstance(artifact, Mapping) and artifact.get(
                                    "ref"
                                ):
                                    refs.append(str(artifact["ref"]))
    for scan_ref in dict.fromkeys(scan_refs):
        scan_evidence = _resolve_json(scan_ref, evidence_root)
        files = scan_evidence.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, Mapping) and item.get("ref"):
                    refs.append(str(item["ref"]))
    unique_refs = list(dict.fromkeys(refs))
    evidence_manifest = []
    for ref in unique_refs:
        raw = _resolve_bytes(ref, evidence_root)
        evidence_manifest.append(
            {"ref": ref, "sha256": hashlib.sha256(raw).hexdigest()}
        )
    manifest = {
        "schemaVersion": WORKFLOW_CHAT_ACCEPTANCE_VERSION,
        "scenarioVersion": WORKFLOW_CHAT_SCENARIO_VERSION,
        "routeInventoryVersion": compatibility_map()["version"],
        "issue": WORKFLOW_CHAT_ACCEPTANCE_ISSUE,
        "parentIssue": WORKFLOW_CHAT_PARENT_ISSUE,
        "status": "passed",
        "generatedAt": source.get("generatedAt"),
        "expiresAt": source.get("expiresAt"),
        "sourceCommit": source.get("sourceCommit"),
        "compatibilityProfile": source.get("compatibilityProfile"),
        "images": dict(source.get("images") or {}),
        "bundleDigests": dict(source.get("bundleDigests") or {}),
        "supersededReportRef": source.get("supersededReportRef"),
        "combinations": {
            str(key): dict(value)
            for key, value in combinations.items()
            if isinstance(value, Mapping)
        },
        "evidenceScans": {
            str(key): dict(value)
            for key, value in scans.items()
            if isinstance(value, Mapping)
        },
        "evidenceManifest": evidence_manifest,
    }
    assert_secret_free(manifest)
    return manifest


def validate_workflow_chat_acceptance_manifest(
    manifest: Mapping[str, Any],
    *,
    evidence_root: Path,
    expected_commit: str | None = None,
    now: datetime | None = None,
) -> None:
    """Fail closed unless the complete protected #3642 matrix is authoritative."""

    if (
        manifest.get("schemaVersion") != WORKFLOW_CHAT_ACCEPTANCE_VERSION
        or manifest.get("scenarioVersion") != WORKFLOW_CHAT_SCENARIO_VERSION
        or manifest.get("routeInventoryVersion") != compatibility_map()["version"]
        or manifest.get("issue") != WORKFLOW_CHAT_ACCEPTANCE_ISSUE
        or manifest.get("parentIssue") != WORKFLOW_CHAT_PARENT_ISSUE
        or manifest.get("status") != "passed"
        or manifest.get("compatibilityProfile") != WORKFLOW_CHAT_COMPATIBILITY_PROFILE
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance identity, scenario, or route-inventory "
            "version is invalid"
        )
    superseded = manifest.get("supersededReportRef")
    if superseded is not None and (
        not isinstance(superseded, str)
        or not superseded.strip()
        or urllib.parse.urlparse(superseded).scheme not in {"", "https"}
        or (
            not urllib.parse.urlparse(superseded).scheme
            and Path(superseded).is_absolute()
        )
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance superseded report ref is not a bounded ref"
        )
    observed_at = now or datetime.now(timezone.utc)
    generated_at = _timestamp(manifest.get("generatedAt"), field="generatedAt")
    expires_at = _timestamp(manifest.get("expiresAt"), field="expiresAt")
    if generated_at > observed_at:
        raise ConformanceContractError("workflow Chat acceptance is future-dated")
    if observed_at - generated_at > MAX_ACCEPTANCE_AGE or expires_at <= observed_at:
        raise ConformanceContractError("workflow Chat acceptance is stale or expired")
    if expires_at <= generated_at:
        raise ConformanceContractError(
            "workflow Chat acceptance validity interval is invalid"
        )
    source_commit = str(manifest.get("sourceCommit") or "")
    if not source_commit or (
        expected_commit is not None and source_commit != expected_commit
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance source commit does not match"
        )
    images = manifest.get("images")
    if not isinstance(images, Mapping):
        raise ConformanceContractError("workflow Chat acceptance images are missing")
    require_pinned_images({str(key): str(value) for key, value in images.items()})
    bundle_digests = manifest.get("bundleDigests")
    if not isinstance(bundle_digests, Mapping) or set(bundle_digests) != set(
        REQUIRED_BUNDLE_DIGESTS
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance bundle digests are incomplete"
        )
    for name in REQUIRED_BUNDLE_DIGESTS:
        _require_sha256_ref(bundle_digests[name], field=f"bundleDigests.{name}")

    evidence_manifest = manifest.get("evidenceManifest")
    if not isinstance(evidence_manifest, list) or not evidence_manifest:
        raise ConformanceContractError(
            "workflow Chat acceptance evidence manifest is missing"
        )
    resolved_raw: dict[str, bytes] = {}
    resolved: dict[str, dict[str, Any]] = {}
    manifest_digests: dict[str, str] = {}
    for item in evidence_manifest:
        ref = item.get("ref") if isinstance(item, Mapping) else None
        digest = item.get("sha256") if isinstance(item, Mapping) else None
        if (
            not isinstance(ref, str)
            or not ref.strip()
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or ref in resolved
        ):
            raise ConformanceContractError(
                "workflow Chat acceptance evidence manifest is malformed"
            )
        raw = _resolve_bytes(ref, evidence_root)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ConformanceContractError(
                f"workflow Chat acceptance evidence digest mismatch: {ref}"
            )
        resolved_raw[ref] = raw
        manifest_digests[ref] = digest
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            resolved[ref] = value

    inventory = workflow_chat_combinations()
    combinations = manifest.get("combinations")
    if not isinstance(combinations, Mapping) or set(combinations) != set(inventory):
        raise ConformanceContractError(
            "workflow Chat acceptance combination coverage is incomplete"
        )
    used_refs: set[str] = set()
    trace_screenshot_digests: set[str] = set()
    for combination_id, combination in inventory.items():
        entry = combinations[combination_id]
        if not isinstance(entry, Mapping):
            raise ConformanceContractError(
                f"workflow Chat acceptance combination is malformed: {combination_id}"
            )
        if (
            entry.get("schemaVersion") != WORKFLOW_CHAT_COMBINATION_VERSION
            or entry.get("combinationId") != combination_id
            or entry.get("harnessId") != combination.harness_id
            or entry.get("hostClassRef") != combination.host_class_ref
            or entry.get("launchPolicyRef") != combination.launch_policy_ref
            or entry.get("executionRealizerRef") != combination.execution_realizer_ref
            or entry.get("hostMode") != combination.host_mode
            or set(
                _require_string_list(
                    entry.get("advertisedCapabilities"),
                    field=f"combinations.{combination_id}.advertisedCapabilities",
                )
            )
            != set(combination.advertised_capabilities)
        ):
            raise ConformanceContractError(
                "workflow Chat acceptance combination identity does not match the "
                f"claimed inventory: {combination_id}"
            )
        if not combination.native_chat_claimed:
            if (
                entry.get("status") != "unsupported"
                or entry.get("unsupportedReason") != combination.unsupported_reason
                or entry.get("rows")
                or entry.get("reports")
                or entry.get("bindingIdentity")
                or entry.get("hostImageRef")
            ):
                raise ConformanceContractError(
                    "workflow Chat acceptance must report an unclaimed combination "
                    f"as unsupported with its stable reason: {combination_id}"
                )
            continue
        if (
            entry.get("status") != "passed"
            or entry.get("unsupportedReason") is not None
        ):
            raise ConformanceContractError(
                f"workflow Chat acceptance combination did not pass: {combination_id}"
            )
        _validate_binding_identity(
            entry.get("bindingIdentity"), combination=combination
        )
        # A combination is only qualified by the host image that actually ran
        # it, so the digest is pinned per host class instead of sharing one
        # ``host`` entry across materially different images.
        host_image_ref = images.get(combination.host_image_key)
        if (
            entry.get("hostImageRef") != host_image_ref
            or _IMAGE_DIGEST_REF.fullmatch(str(host_image_ref or "")) is None
        ):
            raise ConformanceContractError(
                "workflow Chat acceptance combination does not pin the host "
                f"image digest that executed it: {combination_id}"
            )
        cleanup_outcome = entry.get("cleanupOutcome")
        if (
            not isinstance(cleanup_outcome, Mapping)
            or cleanup_outcome.get("providerProfileReleasedLast") is not True
            or cleanup_outcome.get("liveResourcesRemoved")
            is not combination.live_resources_removed_expected
        ):
            raise ConformanceContractError(
                "workflow Chat acceptance combination lacks a complete cleanup "
                f"outcome: {combination_id}"
            )
        _require_string(
            cleanup_outcome.get("cleanupState"),
            field=f"combinations.{combination_id}.cleanupOutcome.cleanupState",
        )
        rows = entry.get("rows")
        if not isinstance(rows, Mapping) or set(rows) != set(
            REQUIRED_WORKFLOW_CHAT_ROWS
        ):
            raise ConformanceContractError(
                "workflow Chat acceptance journey coverage is incomplete: "
                f"{combination_id}"
            )
        global_correlation: dict[str, str] | None = None
        observed_cleanup_state: str | None = None
        row_evidence_refs: dict[str, set[str]] = {}
        for row_name, required_assertions in REQUIRED_WORKFLOW_CHAT_ROWS.items():
            row = rows[row_name]
            if not isinstance(row, Mapping) or row.get("status") != "passed":
                raise ConformanceContractError(
                    f"workflow Chat acceptance row did not pass: {row_name}"
                )
            assertions = row.get("assertions")
            refs = row.get("evidenceRefs")
            if (
                not isinstance(assertions, Mapping)
                or any(assertions.get(name) is not True for name in required_assertions)
                or not isinstance(refs, list)
                or not refs
            ):
                raise ConformanceContractError(
                    f"workflow Chat acceptance row lacks controlling evidence: {row_name}"
                )
            row_evidence_refs[row_name] = {str(ref_value) for ref_value in refs}
            for ref_value in refs:
                ref = str(ref_value)
                evidence = resolved.get(ref)
                evidence_assertions = (
                    evidence.get("assertions")
                    if isinstance(evidence, Mapping)
                    else None
                )
                source_records = (
                    evidence.get("sourceRecords")
                    if isinstance(evidence, Mapping)
                    else None
                )
                if (
                    not isinstance(evidence, Mapping)
                    or evidence.get("schemaVersion")
                    != WORKFLOW_CHAT_CASE_EVIDENCE_VERSION
                    or evidence.get("issue") != WORKFLOW_CHAT_ACCEPTANCE_ISSUE
                    or evidence.get("parentIssue") != WORKFLOW_CHAT_PARENT_ISSUE
                    or evidence.get("row") != row_name
                    or evidence.get("combination") != combination_id
                    or evidence.get("status") != "passed"
                    or evidence.get("sourceCommit") != source_commit
                    or evidence.get("images") != images
                    or evidence.get("stockHostUnmodified") is not True
                    or evidence.get("browserOriginated") is not True
                    or evidence.get("moonmindScopedOnly") is not True
                    or not isinstance(evidence_assertions, Mapping)
                    or any(
                        evidence_assertions.get(name) is not True
                        for name in required_assertions
                    )
                    or not isinstance(source_records, list)
                ):
                    raise ConformanceContractError(
                        f"workflow Chat acceptance case evidence is invalid: {row_name}"
                    )
                used_refs.add(ref)
                sources_by_type: dict[str, Mapping[str, Any]] = {}
                for record in source_records:
                    if not isinstance(record, Mapping):
                        raise ConformanceContractError(
                            f"workflow Chat source record is malformed: {row_name}"
                        )
                    record_type = str(record.get("type") or "")
                    record_ref = str(record.get("ref") or "")
                    record_digest = str(record.get("sha256") or "")
                    if (
                        not record_type
                        or not record_ref
                        or _SHA256.fullmatch(record_digest) is None
                        or record_type in sources_by_type
                    ):
                        raise ConformanceContractError(
                            f"workflow Chat source record is malformed: {row_name}"
                        )
                    source = resolved.get(record_ref)
                    manifest_digest = manifest_digests.get(record_ref, "")
                    if (
                        not isinstance(source, Mapping)
                        or manifest_digest != record_digest
                    ):
                        raise ConformanceContractError(
                            "workflow Chat source record is invalid: "
                            f"{row_name}/{record_type}"
                        )
                    sources_by_type[record_type] = source
                    used_refs.add(record_ref)
                derived_assertions, correlation = validate_workflow_chat_source_records(
                    sources_by_type,
                    row_name=row_name,
                    source_commit=source_commit,
                    images=images,
                    generated_at=generated_at,
                    combination=combination,
                    expected_correlation=global_correlation,
                )
                browser_data = sources_by_type["browserTrace"].get("data")
                if isinstance(browser_data, Mapping):
                    trace_screenshot_digests.add(
                        _require_sha256_ref(
                            browser_data.get("screenshotSha256"),
                            field="browserTrace.data.screenshotSha256",
                        ).removeprefix("sha256:")
                    )
                captured_source = sources_by_type.get("capturedEvidence")
                if captured_source is not None:
                    captured_data = _require_mapping(
                        captured_source.get("data"), field="capturedEvidence.data"
                    )
                    for artifact in _require_evidence_items(
                        captured_data.get("artifacts"),
                        field="capturedEvidence.data.artifacts",
                    ):
                        if manifest_digests.get(artifact["ref"]) != artifact["sha256"]:
                            raise ConformanceContractError(
                                "workflow Chat capturedEvidence artifact is unresolved"
                            )
                        used_refs.add(artifact["ref"])
                if any(
                    evidence_assertions.get(name) is not value
                    or assertions.get(name) is not value
                    for name, value in derived_assertions.items()
                ):
                    raise ConformanceContractError(
                        "workflow Chat row assertions do not match typed source evidence: "
                        f"{row_name}"
                    )
                cleanup_source = sources_by_type.get("cleanupReceipt")
                if cleanup_source is not None:
                    cleanup_data = _require_mapping(
                        cleanup_source.get("data"), field="cleanupReceipt.data"
                    )
                    observed_cleanup_state = str(cleanup_data["cleanupState"])
                if global_correlation is None:
                    global_correlation = correlation

        if observed_cleanup_state != cleanup_outcome["cleanupState"]:
            raise ConformanceContractError(
                "workflow Chat acceptance cleanup outcome does not match the "
                f"typed cleanup receipt: {combination_id}"
            )
        if global_correlation is None:
            raise ConformanceContractError(
                "workflow Chat acceptance combination resolved no session "
                f"correlation: {combination_id}"
            )
        timeline_ref = _require_string(
            entry.get("timelineRef"),
            field=f"combinations.{combination_id}.timelineRef",
        )
        timeline = resolved.get(timeline_ref)
        terminal = timeline.get("terminal") if isinstance(timeline, Mapping) else None
        timeline_cleanup = (
            timeline.get("cleanup") if isinstance(timeline, Mapping) else None
        )
        if (
            not isinstance(timeline, Mapping)
            or timeline.get("sessionId") != global_correlation["bridgeSessionId"]
            or not isinstance(terminal, Mapping)
            or terminal.get("state") not in _TERMINAL_STATES
            or not isinstance(timeline_cleanup, Mapping)
            or timeline_cleanup.get("state") != cleanup_outcome["cleanupState"]
        ):
            raise ConformanceContractError(
                "workflow Chat acceptance operator timeline does not bind the "
                f"terminal, cleaned-up session: {combination_id}"
            )
        used_refs.add(timeline_ref)
        reports = entry.get("reports")
        if not isinstance(reports, list) or not reports:
            raise ConformanceContractError(
                "workflow Chat acceptance report is missing: " f"{combination_id}"
            )
        for report_ref in reports:
            ref = str(report_ref)
            report = resolved.get(ref)
            summary = report.get("summary") if isinstance(report, Mapping) else None
            cases = report.get("cases") if isinstance(report, Mapping) else None
            try:
                failed = (
                    int(summary.get("failed", 1)) if isinstance(summary, Mapping) else 1
                )
                passed = (
                    int(summary.get("passed", 0)) if isinstance(summary, Mapping) else 0
                )
                skipped = (
                    int(summary.get("skipped", 0))
                    if isinstance(summary, Mapping)
                    else 0
                )
            except (TypeError, ValueError) as exc:
                raise ConformanceContractError(
                    "workflow Chat acceptance report summary is malformed"
                ) from exc
            try:
                report_generated_at = _timestamp(
                    report.get("generatedAt") if isinstance(report, Mapping) else None,
                    field="report.generatedAt",
                )
            except ConformanceContractError as exc:
                raise ConformanceContractError(
                    "workflow Chat acceptance references a stale or malformed report"
                ) from exc
            case_statuses: list[str] = []
            case_ids: set[str] = set()
            case_refs_by_id: dict[str, set[str]] = {}
            if isinstance(cases, list):
                for case in cases:
                    case_id = case.get("caseId") if isinstance(case, Mapping) else None
                    case_status = (
                        case.get("status") if isinstance(case, Mapping) else None
                    )
                    case_refs = (
                        case.get("evidenceRefs") if isinstance(case, Mapping) else None
                    )
                    duration_ms = (
                        case.get("durationMs") if isinstance(case, Mapping) else None
                    )
                    if (
                        not isinstance(case_id, str)
                        or not case_id
                        or case_id in case_ids
                        or case_status not in {"passed", "failed", "skipped"}
                        or not isinstance(duration_ms, int)
                        or isinstance(duration_ms, bool)
                        or duration_ms <= 0
                        or not isinstance(case_refs, list)
                        or not case_refs
                        or any(str(item) not in resolved_raw for item in case_refs)
                    ):
                        raise ConformanceContractError(
                            "workflow Chat acceptance report cases are malformed"
                        )
                    case_ids.add(case_id)
                    case_refs_by_id[case_id] = {str(item) for item in case_refs}
                    case_statuses.append(str(case_status))
            computed_summary = {
                status_name: sum(value == status_name for value in case_statuses)
                for status_name in ("passed", "failed", "skipped")
            }
            if (
                not isinstance(report, Mapping)
                or report.get("schemaVersion") != REPORT_VERSION
                or report.get("images") != images
                or not isinstance(summary, Mapping)
                or not case_statuses
                or computed_summary
                != {"passed": passed, "failed": failed, "skipped": skipped}
                or report_generated_at > generated_at
                or generated_at - report_generated_at > timedelta(days=1)
                or failed != 0
                or skipped != 0
                or passed != len(REQUIRED_WORKFLOW_CHAT_ROWS)
            ):
                raise ConformanceContractError(
                    "workflow Chat acceptance references a non-passing report"
                )
            # A report only qualifies this combination when its cases name this
            # combination's rows and reference exactly that row's evidence.
            if case_refs_by_id != {
                workflow_chat_case_id(combination_id, row_name): refs
                for row_name, refs in row_evidence_refs.items()
            }:
                raise ConformanceContractError(
                    "workflow Chat acceptance report does not cover this "
                    f"combination's row evidence: {combination_id}"
                )
            if report.get("authMode") != combination.auth_mode:
                raise ConformanceContractError(
                    "workflow Chat acceptance report publishes the wrong "
                    f"authentication mode: {combination_id}"
                )
            used_refs.add(ref)

    scans = manifest.get("evidenceScans")
    if not isinstance(scans, Mapping) or set(scans) != set(REQUIRED_EVIDENCE_CHANNELS):
        raise ConformanceContractError(
            "workflow Chat acceptance evidence-channel scans are incomplete"
        )
    scanned_screenshot_digests: set[str] = set()
    for channel in REQUIRED_EVIDENCE_CHANNELS:
        scan = scans[channel]
        ref = scan.get("evidenceRef") if isinstance(scan, Mapping) else None
        evidence = resolved.get(str(ref)) if ref else None
        files = evidence.get("files") if isinstance(evidence, Mapping) else None
        if (
            not isinstance(scan, Mapping)
            or scan.get("status") != "passed"
            or not isinstance(ref, str)
            or not isinstance(evidence, Mapping)
            or evidence.get("status") != "passed"
            or evidence.get("channel") != channel
            or not isinstance(files, list)
            or not files
        ):
            raise ConformanceContractError(
                f"workflow Chat acceptance secret scan did not pass: {channel}"
            )
        used_refs.add(ref)
        for item in files:
            file_ref = item.get("ref") if isinstance(item, Mapping) else None
            digest = item.get("sha256") if isinstance(item, Mapping) else None
            if (
                not isinstance(file_ref, str)
                or not isinstance(digest, str)
                or _SHA256.fullmatch(digest) is None
                or manifest_digests.get(file_ref) != digest
            ):
                raise ConformanceContractError(
                    f"workflow Chat raw evidence scan is invalid: {channel}"
                )
            used_refs.add(file_ref)
            if channel == "screenshots":
                scanned_screenshot_digests.add(digest)

    if not trace_screenshot_digests.issubset(scanned_screenshot_digests):
        raise ConformanceContractError(
            "workflow Chat browserTrace screenshot is not scanned evidence"
        )

    if used_refs != set(resolved_raw):
        raise ConformanceContractError(
            "workflow Chat acceptance contains unowned or missing evidence refs"
        )
    assert_secret_free(manifest)
    for raw in resolved_raw.values():
        assert_secret_free(raw.decode("utf-8", errors="replace"))


__all__ = [
    "MAX_ACCEPTANCE_AGE",
    "REQUIRED_BUNDLE_DIGESTS",
    "REQUIRED_WORKFLOW_CHAT_ROWS",
    "REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS",
    "WORKFLOW_CHAT_ACCEPTANCE_ISSUE",
    "WORKFLOW_CHAT_ACCEPTANCE_VERSION",
    "WORKFLOW_CHAT_CASE_EVIDENCE_VERSION",
    "WORKFLOW_CHAT_COMBINATIONS",
    "WORKFLOW_CHAT_COMBINATION_VERSION",
    "WORKFLOW_CHAT_COMPATIBILITY_PROFILE",
    "WORKFLOW_CHAT_PARENT_ISSUE",
    "WORKFLOW_CHAT_SCENARIO_VERSION",
    "WORKFLOW_CHAT_SOURCE_RECORD_VERSION",
    "WorkflowChatCombination",
    "advertised_native_chat_capabilities",
    "build_workflow_chat_acceptance_manifest",
    "validate_workflow_chat_source_records",
    "validate_workflow_chat_acceptance_manifest",
    "workflow_chat_case_id",
    "workflow_chat_combinations",
]
