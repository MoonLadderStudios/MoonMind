"""Inventory specialization stays inside existing native profile authority."""

import copy

import pytest

from moonmind.schemas.container_job_models import ContainerJobSpec
from moonmind.security.service_inventory import parse_service_inventory_plan_arguments
from moonmind.security.workload_profiles import (
    AdmittedAssessmentScope,
    WorkloadProfile,
    resolve_profile_job,
    resolved_job_within_scope,
    selection_within_scope,
    validate_profile_launch,
)
from tests.unit.security.test_native_network import native


def inventory_profile():
    return {
        "profileRef": "service-inventory",
        "imageSourceRef": "repository-security",
        "entrypoint": ["python"],
        "modes": [
            {
                "name": "inspect",
                "actionClass": "service-inventory",
                "argv": ["-I", "-m", "moonmind.security.service_inventory_runner"],
            }
        ],
        "networkMode": "bridge",
        "egressPolicyRef": "native",
        "nativeNetwork": native().model_dump(by_alias=True),
        "outputs": [
            {
                "name": f"service-inventory-{name}",
                "relativePath": f"service-inventory/{name}.json",
            }
            for name in ("execution", "summary")
        ],
        "maxCpuMillis": 2000,
        "maxMemoryMiB": 2048,
        "maxTimeoutSeconds": 120,
    }


def inventory_spec():
    return ContainerJobSpec.model_validate(
        {
            "workloadProfile": {
                "profileRef": "service-inventory",
                "mode": "inspect",
                "assessmentRef": "assessment-1",
                "nativeNetwork": native().model_dump(by_alias=True),
                "serviceInventory": {"bannerPorts": [587]},
            },
            "inputAttachments": [],
            "resources": {"cpuMillis": 1000, "memoryMiB": 128},
            "timeoutSeconds": 75,
        }
    )


def inventory_scope():
    return AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "assessment-1",
            "profileRefs": ["service-inventory"],
            "actionClasses": ["service-inventory"],
            "targets": [{"nativeNetwork": native().model_dump(by_alias=True)}],
            "networkRefs": ["native"],
            "maxJobs": 2,
            "maxJobSeconds": 120,
            "evidenceIntent": "Bounded admitted TCP inventory",
        }
    )


def test_source_free_inventory_renders_fixed_plan_and_preserves_scope():
    profiles = (WorkloadProfile.model_validate(inventory_profile()),)
    original = inventory_spec()
    resolved = resolve_profile_job(original, profiles)
    assert resolved.input_attachments == [] and resolved.workspace_ref is None
    assert resolved.entrypoint == ["python"]
    assert resolved.command[:3] == [
        "-I",
        "-m",
        "moonmind.security.service_inventory_runner",
    ]
    plan = parse_service_inventory_plan_arguments(resolved.command[3:])
    assert plan.native_network == original.workload_profile.native_network
    assert plan.options == original.workload_profile.service_inventory
    assert all(len(arg) <= 4096 for arg in resolved.command)
    assert selection_within_scope(resolved, inventory_scope())
    resolved_job_within_scope(resolved, inventory_scope(), profiles)
    validate_profile_launch(resolved, profiles)


def test_generic_native_profile_still_requires_captured_inputs():
    original = inventory_spec()
    selection = original.workload_profile.model_copy(update={"service_inventory": None})
    with pytest.raises(ValueError, match="captured input"):
        resolve_profile_job(
            original.model_copy(update={"workload_profile": selection}),
            (WorkloadProfile.model_validate(inventory_profile()),),
        )


@pytest.mark.parametrize(
    "change",
    [
        "argv",
        "entrypoint",
        "action",
        "environment",
        "caches",
        "outputs",
        "arguments",
        "credentials",
        "inputs",
        "exceptions",
    ],
)
def test_inventory_rejects_noncanonical_profile_or_request(change):
    profile = inventory_profile()
    payload = inventory_spec().model_dump(by_alias=True)
    if change == "argv":
        profile["modes"][0]["argv"] += ["--script=unbounded"]
    elif change == "entrypoint":
        profile["entrypoint"] = ["sh"]
    elif change == "action":
        profile["modes"][0]["actionClass"] = "source-analysis"
    elif change == "environment":
        profile["environment"] = {"EXTRA": "value"}
    elif change == "caches":
        profile["caches"] = [{"cacheRef": "cache", "target": "/opt/extra"}]
    elif change == "outputs":
        profile["outputs"][0]["relativePath"] = "unrelated.json"
    elif change == "arguments":
        profile["modes"][0]["arguments"] = [
            {"name": "extra", "type": "enum", "values": ["extra"]}
        ]
        payload["workloadProfile"]["arguments"] = {"extra": "extra"}
    elif change == "credentials":
        profile["credentials"] = [
            {
                "name": "app",
                "purpose": "application_auth",
                "delivery": "env",
                "environmentName": "APP_SECRET",
            }
        ]
    elif change == "inputs":
        payload["inputAttachments"] = [{"name": "extra", "artifactRef": "art_extra"}]
    elif change == "exceptions":
        profile["configurationExceptions"] = [{"name": "extra", "argv": ["--extra"]}]
        payload["workloadProfile"]["configurationExceptions"] = ["extra"]
    with pytest.raises(ValueError):
        resolve_profile_job(
            ContainerJobSpec.model_validate(payload),
            (WorkloadProfile.model_validate(profile),),
        )


def test_inventory_worker_rejects_changed_options_or_argv():
    profiles = (WorkloadProfile.model_validate(inventory_profile()),)
    resolved = resolve_profile_job(inventory_spec(), profiles)
    payload = resolved.model_dump(by_alias=True)
    payload["workloadProfile"]["serviceInventory"]["bannerPorts"] = []
    with pytest.raises(ValueError, match="override profile launch"):
        validate_profile_launch(ContainerJobSpec.model_validate(payload), profiles)
    payload = resolved.model_dump(by_alias=True)
    payload["command"][-1] = "invalid"
    with pytest.raises(ValueError, match="override profile launch"):
        validate_profile_launch(ContainerJobSpec.model_validate(payload), profiles)


def test_inventory_keeps_action_and_native_target_scope_checks():
    profiles = (WorkloadProfile.model_validate(inventory_profile()),)
    resolved = resolve_profile_job(inventory_spec(), profiles)
    scope = inventory_scope().model_copy(
        update={"action_classes": ("source-analysis",)}
    )
    with pytest.raises(PermissionError):
        resolved_job_within_scope(resolved, scope, profiles)
    payload = copy.deepcopy(resolved.model_dump(by_alias=True))
    payload["workloadProfile"]["nativeNetwork"]["targets"][0]["hosts"] = [
        "other.example.com"
    ]
    assert not selection_within_scope(
        ContainerJobSpec.model_validate(payload), inventory_scope()
    )
