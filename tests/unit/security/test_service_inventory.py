from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from moonmind.security import service_inventory as module
from moonmind.security.native_network import NativeNetworkSelection
from moonmind.security.service_inventory import (
    MAX_SERVICE_INVENTORY_EXECUTION_BYTES,
    MAX_SERVICE_INVENTORY_PLAN_BYTES,
    ServiceInventoryExecution,
    ServiceInventoryOptions,
    ServiceInventoryPinnedIdentity,
    build_service_inventory_command,
    build_service_inventory_plan,
    parse_service_inventory_plan_arguments,
    sanitize_service_banner,
    serialize_service_inventory_execution,
    service_inventory_pinned_identities,
    service_inventory_plan_arguments,
    summarize_service_inventory,
    validate_service_inventory_execution,
    validate_service_inventory_receipt,
)


def selection(ports=(22, 80), **limits):
    return NativeNetworkSelection.model_validate(
        {
            "targets": [
                {
                    "hosts": ["service.example.com"],
                    "ports": ports,
                    "purpose": "live-target",
                }
            ],
            "limits": {
                "maxConnections": 10,
                "maxBytes": 100000,
                "maxConnectionsPerSecond": 2,
                "maxConcurrentConnections": 2,
                "maxDurationSeconds": 60,
                **limits,
            },
        }
    )


def plan(**options):
    return build_service_inventory_plan(selection(), ServiceInventoryOptions(**options))


def record(endpoint, **updates):
    return {
        "authority": endpoint.authority,
        "probe": endpoint.probe,
        "reachability": "open",
        "probeStatus": "not_requested" if endpoint.probe == "connect" else "empty",
        "bytesSent": 0,
        "bytesReceived": 0,
        "observation": None,
        "httpStatus": None,
        "truncated": False,
        **updates,
    }


def execution(p, records=(), **updates):
    return {
        "schemaVersion": "service-inventory-execution/v1",
        "planKey": p.plan_key,
        "startedAt": "2026-11-08T10:00:00Z",
        "finishedAt": "2026-11-08T10:00:01Z",
        "stopReason": "complete",
        "records": list(records),
        **updates,
    }


def identities(p, address="8.8.8.8"):
    return tuple(
        ServiceInventoryPinnedIdentity(authority=e.authority, address=address)
        for e in p.endpoints
    )


def trusted_summary(*args, **kwargs):
    return summarize_service_inventory(
        *args, attestation_ref="art_attestation", job_state="succeeded", **kwargs
    )


def test_optional_selection_preserves_legacy_profile_shape():
    from moonmind.schemas.container_job_models import WorkloadProfileSelection

    legacy = WorkloadProfileSelection(profileRef="native", mode="service-inventory")
    assert "service_inventory" not in legacy.model_dump()
    assert "serviceInventory" not in legacy.model_dump(by_alias=True)
    selected = WorkloadProfileSelection(
        profileRef="native",
        mode="service-inventory",
        serviceInventory={"bannerPorts": [22]},
    )
    assert selected.service_inventory.banner_ports == (22,)


@pytest.mark.parametrize(
    "options",
    [
        {"script": "nmap"},
        {"command": ["sh"]},
        {"flags": "--script"},
        {"url": "http://other.example.com"},
        {"headers": {"Authorization": "secret"}},
        {"bannerPorts": [True]},
        {"bannerPorts": ["22"]},
        {"bannerPorts": [0]},
        {"httpHeadPorts": [65536]},
        {"bannerPorts": [22], "httpHeadPorts": [22]},
        {"connectTimeoutSeconds": 0},
        {"connectTimeoutSeconds": 31},
        {"probeTimeoutSeconds": 11},
        {"maxBannerBytes": 4097},
    ],
)
def test_options_reject_unbounded_or_untyped_inputs(options):
    with pytest.raises(ValueError):
        ServiceInventoryOptions.model_validate(options)


def test_plan_is_canonical_frozen_and_uses_existing_authority():
    native = selection()
    p = build_service_inventory_plan(native, ServiceInventoryOptions(bannerPorts=[22]))
    assert p.native_network is native
    assert [(e.authority, e.probe) for e in p.endpoints] == [
        ("service.example.com:22", "banner"),
        ("service.example.com:80", "connect"),
    ]
    with pytest.raises(FrozenInstanceError):
        p.endpoints = ()
    with pytest.raises(FrozenInstanceError):
        p.endpoints[0].port = 443
    assert p.plan_key == plan(banner_ports=[22, 22]).plan_key
    assert p.plan_key != plan(http_head_ports=[80]).plan_key
    with pytest.raises(ValueError, match="selected native ports"):
        plan(http_head_ports=[443])


def test_plan_reuses_exclusions_and_deduplicates_targets():
    raw = selection().model_dump(by_alias=True)
    raw["targets"] = [
        {
            "hosts": ["8.8.8.0/30"],
            "ports": [22],
            "exclusions": ["8.8.8.1"],
            "purpose": "live-target",
        }
    ] * 2
    p = build_service_inventory_plan(
        NativeNetworkSelection.model_validate(raw), ServiceInventoryOptions()
    )
    assert [e.authority for e in p.endpoints] == [
        "8.8.8.0:22",
        "8.8.8.2:22",
        "8.8.8.3:22",
    ]


def test_execution_rejects_injected_fields_endpoints_duplicates_and_plan_mismatch():
    p = plan()
    for body in [
        execution(p, [record(p.endpoints[0])], command="anything"),
        execution(p, [record(p.endpoints[0], authority="neighbor.example.com:22")]),
        execution(p, [record(p.endpoints[0], probe="http-head")]),
        execution(p, [record(p.endpoints[0])] * 2),
        execution(p, planKey="0" * 64),
        execution(p, startedAt="2026-11-08T10:00:00"),
        execution(p, finishedAt="2026-11-08T09:00:00Z"),
    ]:
        with pytest.raises(ValueError):
            validate_service_inventory_execution(p, body)


@pytest.mark.parametrize(
    "reachability",
    ["refused", "timed_out", "unreachable", "denied", "untested", "transport_error"],
)
def test_non_open_cannot_claim_observed_banner(reachability):
    p = plan(banner_ports=[22])
    body = execution(
        p,
        [
            record(
                p.endpoints[0],
                reachability=reachability,
                probeStatus="observed",
                bytesReceived=1,
                observation={
                    "text": "x",
                    "source": "peer_banner",
                    "confidence": "unverified",
                },
            )
        ],
    )
    with pytest.raises(ValueError):
        validate_service_inventory_execution(p, body)


def test_capture_sanitizes_secrets_controls_and_does_not_promote_claims():
    p = plan(banner_ports=[22])
    text = sanitize_service_banner(
        b"SSH-2.0-guessed\x1b[31m\npassword=top-secret\x00", 1024
    )
    assert "top-secret" not in text
    assert "\x1b" not in text and "\x00" not in text
    assert "SSH-2.0-guessed" in text
    peer = {"text": text, "source": "peer_banner", "confidence": "unverified"}
    body = execution(
        p,
        [
            record(
                p.endpoints[0],
                probeStatus="observed",
                bytesReceived=50,
                observation=peer,
            )
        ],
    )
    assert (
        validate_service_inventory_execution(p, body)["records"][0]["observation"]
        == peer
    )
    peer["confidence"] = "verified"
    with pytest.raises(ValueError):
        validate_service_inventory_execution(p, body)


def test_capture_bound_does_not_emit_partial_secret_marker():
    assert len(sanitize_service_banner("☃" * 5000, 32).encode()) <= 32
    assert "secret" not in sanitize_service_banner("password=secret", 8)


def test_validation_rejects_unsanitized_peer_data_and_false_byte_counts():
    p = plan(banner_ports=[22])
    for changes in [
        {"bytesReceived": 4097},
        {"bytesReceived": True},
        {"bytesSent": 1},
        {
            "probeStatus": "observed",
            "bytesReceived": 10,
            "observation": {
                "text": "password=secret",
                "source": "peer_banner",
                "confidence": "unverified",
            },
        },
        {"httpStatus": 200},
    ]:
        with pytest.raises(ValueError):
            validate_service_inventory_execution(
                p, execution(p, [record(p.endpoints[0], **changes)])
            )


def test_head_requires_fixed_request_and_truthful_probe_status():
    p = plan(http_head_ports=[80])
    endpoint = p.endpoints[1]
    good = record(
        endpoint,
        probeStatus="observed",
        bytesSent=len(p.http_head_request(endpoint)),
        bytesReceived=19,
        httpStatus=200,
    )
    validate_service_inventory_execution(p, execution(p, [good]))
    assert (
        p.http_head_request(endpoint)
        == b"HEAD / HTTP/1.1\r\nHost: service.example.com:80\r\nConnection: close\r\n\r\n"
    )
    for changes in [
        {"bytesSent": 1},
        {"httpStatus": None},
        {"probeStatus": "malformed"},
    ]:
        with pytest.raises(ValueError):
            validate_service_inventory_execution(p, execution(p, [{**good, **changes}]))


def test_missing_and_interrupted_records_are_explicit_partial_progress():
    p = plan()
    summary = trusted_summary(
        p,
        execution(p, [record(p.endpoints[0])], stopReason="canceled"),
        "art_execution",
        pinned_identities=identities(p),
    )
    assert summary.status == "partial"
    assert [r.reachability for r in summary.endpoints] == ["open", "untested"]
    assert not summary.endpoints[1].absence_comparable
    assert summary.endpoints[0].reachability_comparable


@pytest.mark.parametrize(
    "reachability",
    ["timed_out", "unreachable", "denied", "untested", "transport_error"],
)
def test_uncertain_reachability_never_establishes_absence(reachability):
    p = plan()
    summary = trusted_summary(
        p,
        execution(p, [record(p.endpoints[0], reachability=reachability)]),
        "art_execution",
        pinned_identities=identities(p),
    )
    assert not summary.endpoints[0].absence_comparable
    assert summary.status == "partial"


def test_refusal_is_comparable_only_with_exact_trusted_pin_and_scope():
    p = plan()
    body = execution(p, [record(e, reachability="refused") for e in p.endpoints])
    summary = trusted_summary(p, body, "art_execution", pinned_identities=identities(p))
    assert summary.status == "complete"
    assert all(e.absence_comparable for e in summary.endpoints)
    missing = trusted_summary(p, body, "art_execution")
    assert missing.comparison_key is None
    assert not any(e.absence_comparable for e in missing.endpoints)
    changed_clock = trusted_summary(
        p,
        {**body, "finishedAt": "2026-11-08T10:00:02Z"},
        "art_other",
        pinned_identities=identities(p),
    )
    assert changed_clock.comparison_key == summary.comparison_key
    changed_results = trusted_summary(
        p,
        execution(p, [record(e) for e in p.endpoints]),
        "art_other",
        pinned_identities=identities(p),
    )
    assert changed_results.comparison_key == summary.comparison_key
    changed_pin = trusted_summary(
        p, body, "art_execution", pinned_identities=identities(p, "8.8.4.4")
    )
    assert changed_pin.comparison_key != summary.comparison_key
    narrow = build_service_inventory_plan(
        selection(ports=(22,)), ServiceInventoryOptions()
    )
    assert (
        trusted_summary(
            narrow,
            execution(narrow),
            "art_execution",
            pinned_identities=identities(narrow),
        ).comparison_key
        != summary.comparison_key
    )


def test_truncation_and_failed_optional_probe_remain_partial_while_port_is_open():
    p = plan(banner_ports=[22])
    for changes in [
        {
            "truncated": True,
            "bytesReceived": p.capture_bytes_per_endpoint,
            "probeStatus": "malformed",
        },
        {"probeStatus": "timed_out"},
    ]:
        summary = trusted_summary(
            p,
            execution(p, [record(p.endpoints[0], **changes), record(p.endpoints[1])]),
            "art_execution",
            pinned_identities=identities(p),
        )
        assert summary.endpoints[0].reachability == "open"
        assert summary.endpoints[0].reachability_comparable
        assert summary.status == "partial"


@pytest.mark.parametrize(
    "peer_character,long_authority",
    [(chr(34), False), ("é", False), ("é", True), ("🙂", False)],
)
def test_worst_case_json_escaping_and_endpoint_count_fit_output_budget(
    peer_character, long_authority
):
    ports = (65535,) if long_authority else (1, 2, 3, 4)
    native = selection(ports=ports, maxBytes=100000000, maxConnections=1024).model_dump(
        by_alias=True
    )
    native["targets"][0]["hosts"] = (
        [
            f"{index:03d}." + ("a" * 60 + ".") * 3 + "b" * 61 + ".com"
            for index in range(256)
        ]
        if long_authority
        else ["8.8.8.0/24"]
    )
    p = build_service_inventory_plan(
        NativeNetworkSelection.model_validate(native),
        ServiceInventoryOptions(bannerPorts=ports, maxBannerBytes=4096),
    )
    text = sanitize_service_banner(
        peer_character * p.capture_bytes_per_endpoint, p.capture_bytes_per_endpoint
    )
    body = execution(
        p,
        [
            record(
                e,
                probeStatus="observed",
                bytesReceived=p.capture_bytes_per_endpoint,
                observation={
                    "text": text,
                    "source": "peer_banner",
                    "confidence": "unverified",
                },
                truncated=True,
            )
            for e in p.endpoints
        ],
    )
    assert (
        len(serialize_service_inventory_execution(p, body))
        <= MAX_SERVICE_INVENTORY_EXECUTION_BYTES
    )


def test_global_native_limits_apply_to_execution_evidence():
    native = selection(maxConnections=1, maxBytes=1)
    p = build_service_inventory_plan(native, ServiceInventoryOptions(bannerPorts=[22]))
    with pytest.raises(ValueError):
        validate_service_inventory_execution(
            p, execution(p, [record(e) for e in p.endpoints])
        )
    with pytest.raises(ValueError):
        validate_service_inventory_execution(
            p, execution(p, [record(p.endpoints[0], bytesReceived=2)])
        )


def test_execution_model_has_no_authority_or_resolution_input():
    p = plan()
    with pytest.raises(ValidationError):
        ServiceInventoryExecution.model_validate(execution(p, pinnedIdentities=[]))


def test_canonical_chunked_plan_fits_container_argv_and_roundtrips_1024_endpoints():
    native = selection(ports=(1, 2, 3, 4), maxConnections=1024).model_dump(
        by_alias=True
    )
    native["targets"][0]["hosts"] = ["8.8.8.0/24"]
    p = build_service_inventory_plan(
        NativeNetworkSelection.model_validate(native), ServiceInventoryOptions()
    )
    args = service_inventory_plan_arguments(p)
    command = build_service_inventory_command(p)
    assert args[0] == "/outputs/service-inventory"
    assert len(command) <= 128 and max(map(len, command)) <= 4096
    assert command == ["-I", "-m", "moonmind.security.service_inventory_runner", *args]
    assert parse_service_inventory_plan_arguments(args) == p
    assert len(parse_service_inventory_plan_arguments(args).endpoints) == 1024


@pytest.mark.parametrize(
    "arguments",
    [
        (),
        ("/tmp", "e30="),
        ("/outputs/service-inventory", "!"),
        ("/outputs/service-inventory", "e30="),
        ("/outputs/service-inventory", "A" * 4097),
        ("/outputs/service-inventory", *("A" * 4096 for _ in range(129))),
    ],
)
def test_plan_transport_rejects_corrupt_noncanonical_or_oversized_arguments(arguments):
    with pytest.raises(ValueError):
        parse_service_inventory_plan_arguments(arguments)


def test_plan_output_size_is_checked_before_execution(monkeypatch):
    assert MAX_SERVICE_INVENTORY_PLAN_BYTES < 128 * 4096 * 3 // 4
    monkeypatch.setattr(module, "MAX_SERVICE_INVENTORY_PLAN_BYTES", 10)
    with pytest.raises(ValueError, match="plan.*bound"):
        plan()


def test_sanitized_text_remains_sanitized_after_tiny_byte_truncation():
    from moonmind.utils.logging import redact_sensitive_text

    for limit in range(1, 40):
        text = sanitize_service_banner("SSH-2.0-peer password=secret-value", limit)
        assert len(text.encode()) <= limit
        assert redact_sensitive_text(text) == text


def test_retained_summary_rejects_false_comparable_absence_and_complete_status():
    from moonmind.security.service_inventory import ServiceInventorySummary

    p = plan()
    summary = trusted_summary(p, execution(p), "art_execution").model_dump(
        by_alias=True, mode="json"
    )
    variants = []
    false_absence = {
        **summary,
        "endpoints": [dict(row) for row in summary["endpoints"]],
    }
    false_absence["endpoints"][0]["absenceComparable"] = True
    variants.append(false_absence)
    variants.append({**summary, "status": "complete", "limitations": []})
    variants.append({**summary, "endpoints": [summary["endpoints"][0]] * 2})
    for invalid in variants:
        with pytest.raises(ValueError):
            ServiceInventorySummary.model_validate(invalid)


def test_plan_transport_uses_multiple_canonical_bounded_chunks():
    native = selection(ports=(22,)).model_dump(by_alias=True)
    native["targets"][0]["hosts"] = [
        f"host-{index:03d}.inventory.example.com" for index in range(256)
    ]
    p = build_service_inventory_plan(
        NativeNetworkSelection.model_validate(native),
        ServiceInventoryOptions(bannerPorts=[22]),
    )
    args = service_inventory_plan_arguments(p)
    assert len(args) > 2
    assert all(len(chunk) == 4096 for chunk in args[1:-1])
    assert parse_service_inventory_plan_arguments(args) == p
    repartitioned = (args[0], args[1][:-1], args[1][-1:] + args[2], *args[3:])
    with pytest.raises(ValueError):
        parse_service_inventory_plan_arguments(repartitioned)


def test_private_comparison_requires_collector_container_identity():
    native = selection(ports=(22,)).model_dump(by_alias=True)
    native["targets"][0].update(
        hosts=["10.0.0.1"], targetNetworkRef="test-net", targetContainerRef="test-peer"
    )
    p = build_service_inventory_plan(
        NativeNetworkSelection.model_validate(native), ServiceInventoryOptions()
    )
    body = execution(p, [record(p.endpoints[0], reachability="refused")])
    identity = ServiceInventoryPinnedIdentity(
        authority="10.0.0.1:22", address="10.0.0.1"
    )
    missing = trusted_summary(p, body, "art_execution", pinned_identities=(identity,))
    assert missing.comparison_key is None
    assert not missing.endpoints[0].absence_comparable
    pinned = trusted_summary(
        p,
        body,
        "art_execution",
        pinned_identities=(
            identity.model_copy(
                update={"target_identity": "container-and-network-digest"}
            ),
        ),
    )
    assert pinned.comparison_key
    assert pinned.endpoints[0].absence_comparable


def resolution(p):
    from moonmind.security.egress_policy import _digest

    addresses = {
        endpoint.host: [endpoint.host if endpoint.host[0].isdigit() else "8.8.8.8"]
        for endpoint in p.endpoints
    }
    return {
        "nativeSelectionDigest": _digest(
            p.native_network.model_dump(mode="json", by_alias=True)
        ),
        "nativeLimits": p.native_network.limits.model_dump(mode="json", by_alias=True),
        "nativeConnectTimeoutSeconds": p.options.connect_timeout_seconds,
        "resolution": "validated_pinned_job_snapshot",
        "coverage": "complete",
        "limitations": [],
        "deniedConnectionCount": 0,
        "transportErrorCount": 0,
        "denialDiagnostics": [],
        "gatewayRef": "native-gateway",
        "expiresAt": 1800000000,
        "resolvedAddresses": addresses,
        "targetIdentities": {
            target.target_container_ref: "container-identity"
            for target in p.native_network.targets
            if target.target_container_ref
        },
        "nativeNetwork": {
            "schemaVersion": 1,
            "leaseId": "native-gateway",
            "configDigest": "sha256:" + "a" * 64,
            "deadlineUnix": 1800000000,
            "pid": 10,
            "processStart": "1234",
            "status": "running",
            "attempts": len(p.endpoints),
            "activeConnections": 0,
            "bytesReserved": 0,
            "denials": {},
            "transportErrors": {},
            "dialOutcomes": {"open": len(p.endpoints)},
            "endpoints": [
                {
                    "authority": endpoint.authority,
                    "address": min(addresses[endpoint.host]),
                    "port": endpoint.port,
                }
                for endpoint in p.endpoints
            ],
        },
    }


def test_summary_retains_canonical_settings_and_attestation_reference():
    from moonmind.security.service_inventory import ServiceInventorySummary

    p = plan(bannerPorts=[22])
    result = trusted_summary(
        p, execution(p), "art_execution", pinned_identities=identities(p)
    )
    assert result.native_network == p.native_network
    assert result.service_inventory == p.options
    assert result.attestation_ref == "art_attestation"
    assert result.job_state == "succeeded"
    assert (
        build_service_inventory_plan(result.native_network, result.service_inventory)
        == p
    )
    raw = result.model_dump(by_alias=True, mode="json")
    for field in ("nativeNetwork", "serviceInventory"):
        invalid = {key: value for key, value in raw.items() if key != field}
        with pytest.raises(ValueError):
            ServiceInventorySummary.model_validate(invalid)
    for mutation in (
        {"serviceInventory": {}},
        {"planKey": "0" * 64},
        {"captureBytesPerEndpoint": 1},
    ):
        with pytest.raises(ValueError):
            ServiceInventorySummary.model_validate({**raw, **mutation})


@pytest.mark.parametrize("state", [None, "failed", "canceled", "timed_out", "rejected"])
def test_terminal_job_state_cannot_promote_complete_runner_output(state):
    from moonmind.security.service_inventory import ServiceInventorySummary

    p = plan()
    body = execution(
        p, [record(endpoint, reachability="refused") for endpoint in p.endpoints]
    )
    summary = summarize_service_inventory(
        p,
        body,
        "art_execution",
        pinned_identities=identities(p),
        attestation_ref="art_attestation",
        job_state=state,
    )
    assert summary.status == "partial"
    assert summary.comparison_key is None
    assert not any(endpoint.absence_comparable for endpoint in summary.endpoints)
    assert "job_not_succeeded" in summary.limitations
    with pytest.raises(ValueError):
        ServiceInventorySummary.model_validate(
            {
                **summary.model_dump(mode="json", by_alias=True),
                "status": "complete",
                "limitations": [],
            }
        )


def test_missing_retained_attestation_remains_partial_even_with_claimed_pins():
    p = plan()
    body = execution(
        p, [record(endpoint, reachability="refused") for endpoint in p.endpoints]
    )
    summary = summarize_service_inventory(
        p, body, "art_execution", pinned_identities=identities(p), job_state="succeeded"
    )
    assert summary.status == "partial"
    assert summary.comparison_key is None
    assert not any(endpoint.absence_comparable for endpoint in summary.endpoints)
    assert "attestation_unavailable" in summary.limitations


def test_retained_attestation_derives_exact_pins_from_existing_native_evidence():
    p = plan()
    assert service_inventory_pinned_identities(p, resolution(p)) == identities(p)


@pytest.mark.parametrize(
    "change",
    [
        {"nativeSelectionDigest": "sha256:" + "0" * 64},
        {"nativeLimits": {}},
        {"coverage": "partial"},
        {"resolution": "unverified"},
        {"limitations": ["missing"]},
        {"deniedConnectionCount": 1},
        {"transportErrorCount": 1},
        {"deniedConnectionCount": False},
        {"targetIdentities": {"invented": "identity"}},
        {"resolvedAddresses": {}},
        {"evidenceError": "error"},
    ],
)
def test_unusable_or_mismatched_native_attestation_cannot_supply_pins(change):
    p = plan()
    with pytest.raises(ValueError):
        service_inventory_pinned_identities(p, {**resolution(p), **change})


@pytest.mark.parametrize(
    "change",
    [
        {"activeConnections": 1},
        {"attempts": True},
        {"attempts": 11},
        {"bytesReserved": 100001},
        {"leaseId": "different"},
        {"deadlineUnix": 1800000001},
        {"configDigest": "invalid"},
        {"denials": {"bytes": 1}},
        {"transportErrors": {"io": 1}},
        {"dialOutcomes": {}},
        {"endpoints": []},
        {"status": "starting"},
    ],
)
def test_native_counter_gaps_never_establish_final_usable_coverage(change):
    p = plan()
    raw = resolution(p)
    raw["nativeNetwork"].update(change)
    with pytest.raises(ValueError):
        service_inventory_pinned_identities(p, raw)


def test_native_attestation_binds_actual_selected_address_not_another_dns_answer():
    p = plan()
    raw = resolution(p)
    raw["resolvedAddresses"]["service.example.com"] = ["8.8.4.4", "8.8.8.8"]
    with pytest.raises(ValueError):
        service_inventory_pinned_identities(p, raw)
    for endpoint in raw["nativeNetwork"]["endpoints"]:
        endpoint["address"] = "8.8.4.4"
    assert {pin.address for pin in service_inventory_pinned_identities(p, raw)} == {
        "8.8.4.4"
    }


def test_attestation_numeric_limits_and_endpoint_ports_are_strict_not_boolean():
    p = build_service_inventory_plan(
        selection(ports=(1,), maxConnections=1), ServiceInventoryOptions()
    )
    raw = resolution(p)
    raw["nativeLimits"]["maxConnections"] = True
    with pytest.raises(ValueError):
        service_inventory_pinned_identities(p, raw)
    raw = resolution(p)
    raw["nativeNetwork"]["endpoints"][0]["port"] = True
    with pytest.raises(ValueError):
        service_inventory_pinned_identities(p, raw)


def test_receipt_validation_binds_known_aggregate_outcomes_without_inventing_endpoint_attestation():
    p = plan()
    raw = resolution(p)
    body = execution(p, [record(endpoint) for endpoint in p.endpoints])
    validate_service_inventory_receipt(p, body, raw)
    forged = execution(
        p, [record(endpoint, reachability="refused") for endpoint in p.endpoints]
    )
    with pytest.raises(ValueError):
        validate_service_inventory_receipt(p, forged, raw)
    raw["nativeNetwork"]["attempts"] = 3
    raw["nativeNetwork"]["dialOutcomes"] = {"open": 3}
    with pytest.raises(ValueError):
        validate_service_inventory_receipt(p, body, raw)


def test_receipt_validation_preserves_unknown_and_incomplete_progress():
    p = plan()
    raw = resolution(p)
    raw["nativeNetwork"]["attempts"] = 0
    raw["nativeNetwork"]["dialOutcomes"] = {}
    validate_service_inventory_receipt(
        p,
        execution(
            p,
            [record(p.endpoints[0], reachability="transport_error")],
            stopReason="transport_unavailable",
        ),
        raw,
    )
    raw = resolution(p)
    validate_service_inventory_receipt(
        p, execution(p, [record(p.endpoints[0])], stopReason="canceled"), raw
    )


def test_receipt_validation_cannot_claim_more_bytes_than_trusted_transport_reserved():
    p = plan(bannerPorts=[22])
    body = execution(
        p,
        [
            record(
                p.endpoints[0],
                probeStatus="observed",
                bytesReceived=3,
                observation={
                    "text": "abc",
                    "source": "peer_banner",
                    "confidence": "unverified",
                },
            )
        ],
    )
    raw = resolution(p)
    with pytest.raises(ValueError):
        validate_service_inventory_receipt(p, body, raw)
    raw["nativeNetwork"]["bytesReserved"] = 3
    validate_service_inventory_receipt(p, body, raw)


def test_receipt_mismatch_marks_partial_without_rewriting_observed_endpoints():
    p = plan()
    summary = trusted_summary(
        p,
        execution(p, [record(endpoint) for endpoint in p.endpoints]),
        "art_execution",
        pinned_identities=identities(p),
        native_receipt_mismatch=True,
    )
    assert summary.status == "partial"
    assert summary.comparison_key is None
    assert "native_receipt_mismatch" in summary.limitations
    assert all(
        endpoint.reachability == "open" and not endpoint.reachability_comparable
        for endpoint in summary.endpoints
    )


def test_private_attestation_requires_and_binds_existing_container_identity():
    native = selection(ports=(22,)).model_dump(by_alias=True)
    native["targets"][0].update(
        hosts=["10.0.0.1"], targetNetworkRef="test-net", targetContainerRef="test-peer"
    )
    p = build_service_inventory_plan(
        NativeNetworkSelection.model_validate(native), ServiceInventoryOptions()
    )
    raw = resolution(p)
    first = service_inventory_pinned_identities(p, raw)
    assert first[0].target_identity
    raw["targetIdentities"]["test-peer"] = "different-container-identity"
    assert (
        service_inventory_pinned_identities(p, raw)[0].target_identity
        != first[0].target_identity
    )
    raw["targetIdentities"] = {}
    with pytest.raises(ValueError):
        service_inventory_pinned_identities(p, raw)


@pytest.mark.parametrize(
    "timeout", [None, True, 5.0, float("inf"), float("nan"), 0, 31, "5", 6]
)
def test_inventory_pins_require_exact_typed_dial_timeout(timeout):
    p = plan()
    retained = resolution(p)
    if timeout is None:
        retained.pop("nativeConnectTimeoutSeconds")
    else:
        retained["nativeConnectTimeoutSeconds"] = timeout
    with pytest.raises(ValueError):
        service_inventory_pinned_identities(p, retained)


def mixed_width_gateway_resolution(p):
    """Use the actual gateway serialization, which sorts authority strings."""
    from tests.unit.security.test_native_tcp_gateway import config

    retained = resolution(p)
    gateway = config(80, endpoints=retained["nativeNetwork"]["endpoints"])
    retained["nativeNetwork"]["endpoints"] = gateway.to_mapping()["endpoints"]
    return retained


def test_mixed_width_ports_accept_exact_gateway_endpoint_order():
    p = build_service_inventory_plan(
        selection(ports=(80, 443)), ServiceInventoryOptions()
    )
    retained = mixed_width_gateway_resolution(p)
    assert [endpoint.port for endpoint in p.endpoints] == [80, 443]
    assert [
        endpoint["port"] for endpoint in retained["nativeNetwork"]["endpoints"]
    ] == [443, 80]
    assert service_inventory_pinned_identities(p, retained) == identities(p)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "missing",
        "extra",
        "outside",
        "extra_field",
        "bad_authority",
        "boolean_port",
        "wrong_address",
    ],
)
def test_order_independent_receipts_still_require_exact_endpoint_membership(change):
    p = build_service_inventory_plan(
        selection(ports=(80, 443)), ServiceInventoryOptions()
    )
    retained = mixed_width_gateway_resolution(p)
    endpoints = retained["nativeNetwork"]["endpoints"]
    if change == "duplicate":
        endpoints[1] = dict(endpoints[0])
    elif change == "missing":
        endpoints.pop()
    elif change == "extra":
        endpoints.append(dict(endpoints[0]))
    elif change == "outside":
        endpoints[0].update(authority="outside.example.com:443")
    elif change == "extra_field":
        endpoints[0]["untrusted"] = True
    elif change == "bad_authority":
        endpoints[0]["authority"] = []
    elif change == "boolean_port":
        endpoints[0]["port"] = True
    else:
        endpoints[0]["address"] = "8.8.4.4"
    with pytest.raises(ValueError):
        service_inventory_pinned_identities(p, retained)
