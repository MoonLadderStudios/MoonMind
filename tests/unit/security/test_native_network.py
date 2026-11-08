import pytest

from moonmind.security.native_network import (
    NativeNetworkSelection,
    require_native_subset,
)


def native(host="smtp.example.com", **limits):
    return NativeNetworkSelection.model_validate(
        {
            "targets": [{"hosts": [host], "ports": [587], "purpose": "live-target"}],
            "limits": {
                "maxConnections": 10,
                "maxBytes": 4096,
                "maxConnectionsPerSecond": 2,
                "maxConcurrentConnections": 2,
                "maxDurationSeconds": 60,
                **limits,
            },
        }
    )


def test_native_subset_is_exact_and_bounded():
    require_native_subset(native(), native())
    with pytest.raises(ValueError):
        require_native_subset(native("other.example.com"), native())
    with pytest.raises(ValueError):
        require_native_subset(native(maxBytes=4097), native())


@pytest.mark.parametrize(
    "host",
    [
        "*",
        "2130706433",
        "0177.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "::1",
        "::ffff:8.8.8.8",
        "0.0.0.0/0",
        "host.docker.internal",
        "metadata.google.internal",
        "10.0.0.0/8",
    ],
)
def test_ambiguous_unsafe_or_unbounded_targets_are_rejected(host):
    with pytest.raises(ValueError):
        native(host)


def test_canonical_cidr_expansion():
    selection = native("8.8.8.0/30")
    assert selection.targets[0].endpoints() == tuple(
        (f"8.8.8.{i}", 587) for i in range(4)
    )


def test_native_optional_fields_preserve_legacy_shapes():
    from moonmind.schemas.container_job_models import WorkloadProfileSelection
    from moonmind.security.workload_profiles import AssessmentTarget

    assert "nativeNetwork" not in WorkloadProfileSelection(
        profileRef="test", mode="test"
    ).model_dump(by_alias=True)
    assert "native_network" not in AssessmentTarget(workspacePath=".").model_dump()


def test_native_scope_has_one_global_budget():
    from moonmind.security.workload_profiles import AdmittedAssessmentScope

    scope = {
        "assessmentRef": "a",
        "profileRefs": ["p"],
        "actionClasses": ["probe"],
        "targets": [{"nativeNetwork": native().model_dump()}],
        "maxJobs": 5,
        "maxJobSeconds": 60,
        "evidenceIntent": "inspect",
    }
    AdmittedAssessmentScope.model_validate(scope)
    with pytest.raises(ValueError):
        AdmittedAssessmentScope.model_validate(
            {**scope, "targets": scope["targets"] * 2}
        )


def test_exclusions_survive_dns_authority_subset():
    authority = native().model_dump(by_alias=True)
    authority["targets"][0]["exclusions"] = ["8.8.8.8"]
    with pytest.raises(ValueError, match="exclusions"):
        require_native_subset(
            native(), NativeNetworkSelection.model_validate(authority)
        )


@pytest.mark.asyncio
async def test_failed_and_cancelled_native_reservations_never_refund():
    from types import SimpleNamespace

    from api_service.services.assessment_authorizations import (
        assessment_reserved_budget,
    )
    from moonmind.schemas.container_job_models import ContainerJobSubmitRequest

    request = ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "native",
            "source": {"source": "workflow", "workflowId": "w"},
            "spec": {
                "workloadProfile": {
                    "profileRef": "p",
                    "mode": "probe",
                    "assessmentRef": "a",
                    "nativeNetwork": native().model_dump(),
                },
                "inputAttachments": [{"name": "ca", "artifactRef": "art_ca"}],
                "resources": {"cpuMillis": 1000, "memoryMiB": 128},
                "timeoutSeconds": 60,
            },
        }
    )
    records = [
        SimpleNamespace(
            request_json=request.model_dump(by_alias=True, mode="json"),
            source_json=request.source.model_dump(by_alias=True, mode="json"),
            state=state,
        )
        for state in ["failed", "cancelled"]
    ]

    class Session:
        async def scalars(self, statement):
            return SimpleNamespace(all=lambda: records)

    count, seconds, native_reserved = await assessment_reserved_budget(
        Session(), SimpleNamespace(assessment_ref="a"), include_native=True
    )
    assert (count, seconds) == (2, 120)
    assert native_reserved == {
        "max_connections": 20,
        "max_bytes": 8192,
        "max_connections_per_second": 4,
        "max_concurrent_connections": 4,
    }


@pytest.mark.parametrize(
    "gap", ["profile", "scope", "policy", "unmarked", "changed-worker", "mixed-http"]
)
def test_native_authority_is_intersection_at_real_boundaries(gap):
    from dataclasses import replace

    from moonmind.schemas.container_job_models import ContainerJobSubmitRequest
    from moonmind.security.egress_policy import ScopedEgressPolicy
    from moonmind.security.workload_profiles import (
        AdmittedAssessmentScope,
        WorkloadProfile,
        resolve_profile_job,
        resolved_job_within_scope,
        validate_profile_launch,
    )
    from tests.unit.security.test_workload_profiles import (
        PROFILE,
        SCOPE,
        scoped,
        settings,
    )

    selection = native()
    profile_data = {
        **PROFILE,
        "networkMode": "bridge",
        "egressPolicyRef": "native",
        "nativeNetwork": selection.model_dump(),
    }
    policy_data = {
        "policyRef": "native",
        "allowedWorkflowIds": ["workflow-1"],
        "allowedImageSourceRefs": ["repository-security"],
        "nativeNetwork": selection.model_dump(),
    }
    scope_data = {
        **SCOPE,
        "networkRefs": ["native"],
        "targets": [*SCOPE["targets"], {"nativeNetwork": selection.model_dump()}],
    }
    profile = WorkloadProfile.model_validate(profile_data)
    spec = scoped(nativeNetwork=selection.model_dump())
    if gap == "profile":
        profile = WorkloadProfile.model_validate(
            {**profile_data, "nativeNetwork": None}
        )
    elif gap == "unmarked":
        spec.workload_profile.assessment_ref = None
    if gap in {"profile", "unmarked"}:
        with pytest.raises(ValueError):
            resolve_profile_job(spec, (profile,))
        return
    resolved = resolve_profile_job(spec, (profile,))
    if gap == "scope":
        scope_data["targets"] = SCOPE["targets"]
        with pytest.raises(PermissionError):
            resolved_job_within_scope(
                resolved, AdmittedAssessmentScope.model_validate(scope_data), (profile,)
            )
    elif gap == "changed-worker":
        changed = WorkloadProfile.model_validate(
            {**profile_data, "nativeNetwork": native("other.example.com").model_dump()}
        )
        with pytest.raises(ValueError):
            validate_profile_launch(resolved, (changed,))
    elif gap == "mixed-http":
        with pytest.raises(ValueError):
            ScopedEgressPolicy.model_validate(
                {
                    **policy_data,
                    "destinations": [
                        {
                            "host": "smtp.example.com",
                            "purpose": "live-target",
                            "protocol": "https",
                            "port": 443,
                        }
                    ],
                }
            )
    else:
        policy = ScopedEgressPolicy.model_validate(
            {**policy_data, "nativeNetwork": native("other.example.com").model_dump()}
        )
        backend = replace(
            settings(), workload_profiles=(profile,), egress_policies=(policy,)
        )
        request = ContainerJobSubmitRequest(
            idempotencyKey="test",
            source={"source": "workflow", "workflowId": "workflow-1"},
            spec=resolved,
        )
        with pytest.raises(ValueError):
            backend.authorize_egress(request)


def test_address_port_product_and_exclusion_expansion_are_bounded():
    data = native().model_dump(by_alias=True)
    data["targets"][0].update(hosts=["8.8.8.0/24"], ports=[1, 2, 3, 4, 5])
    with pytest.raises(ValueError):
        NativeNetworkSelection.model_validate(data)
    data["targets"][0].update(
        hosts=["smtp.example.com"], ports=[587], exclusions=["8.8.8.0/24", "8.8.9.0/24"]
    )
    with pytest.raises(ValueError):
        NativeNetworkSelection.model_validate(data)


@pytest.mark.parametrize(
    "host", ["168.63.129.16", "kubernetes.default.svc", "api.cluster.local"]
)
def test_control_plane_names_and_addresses_are_never_targets(host):
    with pytest.raises(ValueError):
        native(host)


def test_narrowed_target_does_not_inherit_unrelated_host_exclusions():
    authority = native().model_dump(by_alias=True)
    authority["targets"] = [
        *authority["targets"],
        {
            "hosts": ["other.example.com"],
            "ports": [587],
            "purpose": "live-target",
            "exclusions": ["8.8.8.8"],
        },
    ]
    require_native_subset(native(), NativeNetworkSelection.model_validate(authority))


@pytest.mark.parametrize("ports", [None, 443, "443", {443: "ignored"}])
def test_native_ports_require_a_sequence_not_coercible_container(ports):
    data = native().model_dump(by_alias=True)
    data["targets"][0]["ports"] = ports
    with pytest.raises(ValueError):
        NativeNetworkSelection.model_validate(data)
