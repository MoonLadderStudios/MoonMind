"""Deployment workload profiles and admitted assessment scope (#5035)."""

from __future__ import annotations

import json

import pytest

from moonmind.config.container_backend_settings import (
    CACHE_SOURCES_ENV_KEY,
    IMAGE_SOURCES_ENV_KEY,
    ContainerBackendConfigError,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import ContainerJobSpec
from moonmind.security.container_job_capabilities import (
    ContainerJobCapabilityError,
    assessment_scope_from_policy,
    mint_container_job_session_capability,
    verify_container_job_session_capability,
)
from moonmind.security.workload_profiles import (
    WORKLOAD_PROFILES_ENV_KEY,
    AdmittedAssessmentScope,
    resolve_profile_job,
    resolved_job_within_scope,
    selection_within_scope,
    validate_profile_launch,
)

PROFILE = {
    "profileRef": "source-scan",
    "imageSourceRef": "repository-security",
    "entrypoint": ["scanner"],
    "modes": [
        {
            "name": "scan",
            "actionClass": "source-analysis",
            "argv": [
                "scan",
                "--severity={severity}",
                "--max-findings={limit}",
                "{target}",
                "--report=/outputs/report.json",
            ],
            "arguments": [
                {"name": "severity", "type": "enum", "values": ["low", "high"]},
                {"name": "limit", "type": "integer", "minimum": 1, "maximum": 500},
                {"name": "target", "type": "input", "required": True},
            ],
        },
        {
            "name": "probe",
            "actionClass": "live-probe",
            "argv": ["probe", "--route={route}", "{target}"],
            "arguments": [
                {"name": "route", "type": "string", "pattern": "/[a-z/]{0,64}"},
                {"name": "target", "type": "input", "required": True},
            ],
        },
    ],
    "environment": {"SCANNER_IGNORE_REPOSITORY_CONFIG": "1"},
    "caches": [{"cacheRef": "scanner-rules", "target": "/opt/rules"}],
    "configurationExceptions": [
        {"name": "inline-suppressions", "argv": ["--honor-inline-suppressions"]}
    ],
    "outputs": [{"name": "report", "relativePath": "report.json"}],
    "maxCpuMillis": 2000,
    "maxMemoryMiB": 2048,
    "maxTimeoutSeconds": 900,
}

SCOPE = {
    "assessmentRef": "assessment-1",
    "profileRefs": ["source-scan"],
    "actionClasses": ["source-analysis"],
    "targets": [{"workspacePath": "app"}, {"artifactRef": "art_sbom1"}],
    "exclusions": ["app/vendor"],
    "configurationExceptions": [],
    "maxJobs": 2,
    "maxJobSeconds": 600,
    "evidenceIntent": "Source findings for app routes",
}


def settings(profile=PROFILE, *, cache_read_only=True):
    return resolve_container_backend_settings(
        {
            IMAGE_SOURCES_ENV_KEY: json.dumps(
                [{"sourceRef": "repository-security", "image": "ghcr.io/x/scan:1"}]
            ),
            CACHE_SOURCES_ENV_KEY: json.dumps(
                [
                    {
                        "cacheRef": "scanner-rules",
                        "volumeName": "scanner_rules",
                        "target": "/opt/rules",
                        "readOnly": cache_read_only,
                    }
                ]
            ),
            WORKLOAD_PROFILES_ENV_KEY: json.dumps([profile]),
        }
    )


def spec(arguments=None, *, mode="scan", inputs=None, **fields):
    return ContainerJobSpec.model_validate(
        {
            "workloadProfile": {
                "profileRef": "source-scan",
                "mode": mode,
                "arguments": ({"target": "source"} if arguments is None else arguments),
                **fields.pop("selection", {}),
            },
            "workspaceRef": {"kind": "sandbox", "workspaceId": "sandbox-1"},
            "inputAttachments": inputs
            or [{"name": "source", "workspacePath": "app/routes"}],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            "timeoutSeconds": 300,
            **fields,
        }
    )


PROFILES = settings().workload_profiles


@pytest.mark.parametrize(
    ("arguments", "command"),
    [
        (
            {"target": "source"},
            ["scan", "/inputs/source", "--report=/outputs/report.json"],
        ),
        (
            {"target": "source/routes", "severity": "high", "limit": 25},
            [
                "scan",
                "--severity=high",
                "--max-findings=25",
                "/inputs/source/routes",
                "--report=/outputs/report.json",
            ],
        ),
    ],
)
def test_approved_variations_render_deployment_owned_launch(arguments, command):
    resolved = resolve_profile_job(spec(arguments), PROFILES)

    assert resolved.image_source_ref == "repository-security"
    assert resolved.entrypoint == ["scanner"]
    assert resolved.command == command
    assert resolved.workspace_read_only is True
    assert resolved.network_mode == "none"
    assert [(e.name, e.value) for e in resolved.environment] == [
        ("SCANNER_IGNORE_REPOSITORY_CONFIG", "1")
    ]
    assert [(c.cache_ref, c.read_only) for c in resolved.caches] == [
        ("scanner-rules", True)
    ]
    # An exact replay of the resolved request renders identically, and the
    # worker accepts it while the profile is unchanged.
    assert resolve_profile_job(resolved, PROFILES) == resolved
    validate_profile_launch(resolved, PROFILES)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"target": "other"},
        {"target": "source/../escape"},
        {"target": "/etc"},
        {"target": "source", "severity": "critical"},
        {"target": "source", "limit": 0},
        {"target": "source", "limit": True},
        {"target": "source", "limit": "25"},
        {"target": "source", "flags": "--config=/workspace/.semgrep.yml"},
    ],
)
def test_typed_argument_constraints_refuse_unapproved_values(arguments):
    with pytest.raises(ValueError):
        resolve_profile_job(spec(arguments), PROFILES)


@pytest.mark.parametrize("route", ["/admin;rm", "-x", "/a\n--config=x", "admin"])
def test_string_arguments_must_match_their_pattern(route):
    with pytest.raises(ValueError):
        resolve_profile_job(
            spec({"target": "source", "route": route}, mode="probe"), PROFILES
        )


@pytest.mark.parametrize(
    "override",
    [
        {"imageSourceRef": "moonmind-python-tests"},
        {"entrypoint": ["sh", "-c"]},
        {"command": ["scan", "--config=/inputs/source/.scanner.yml"]},
        {"environment": [{"name": "SCANNER_CONFIG", "value": "/inputs/source/x"}]},
        {"caches": [{"cacheRef": "scanner-rules", "target": "/opt/rules"}]},
        {"workdir": "/workspace/repo"},
        {"outputs": [{"name": "other", "relativePath": "other.json"}]},
        {"workspaceReadOnly": False},
    ],
)
def test_profile_jobs_cannot_override_profile_owned_content(override):
    with pytest.raises(ValueError):
        resolve_profile_job(spec(**override), PROFILES)


@pytest.mark.parametrize(
    "fields",
    [
        {"image": "alpine"},
        {"repositoryScan": {"scanner": "gitleaks", "outputDirectory": "out"}},
    ],
)
def test_profile_jobs_cannot_carry_another_image_authority(fields):
    with pytest.raises(ValueError):
        spec(**fields)


def test_profile_jobs_require_captured_inputs():
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(
            {
                "workloadProfile": {"profileRef": "source-scan", "mode": "scan"},
                "workspaceRef": {"kind": "sandbox", "workspaceId": "sandbox-1"},
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            }
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"resources": {"cpuMillis": 4000, "memoryMiB": 512}},
        {"resources": {"cpuMillis": 1000, "memoryMiB": 4096}},
        {"timeoutSeconds": 1200},
    ],
)
def test_resources_and_timeouts_stay_within_the_profile(fields):
    with pytest.raises(ValueError, match="profile"):
        resolve_profile_job(spec(**fields), PROFILES)


def test_configuration_exceptions_are_fixed_profile_text():
    resolved = resolve_profile_job(
        spec(selection={"configurationExceptions": ["inline-suppressions"]}),
        PROFILES,
    )
    assert resolved.command[-1] == "--honor-inline-suppressions"
    with pytest.raises(ValueError):
        resolve_profile_job(
            spec(selection={"configurationExceptions": ["repository-rules"]}),
            PROFILES,
        )


@pytest.mark.parametrize("change", ["argv", "image", "removed"])
def test_worker_refuses_content_the_current_profile_no_longer_renders(change):
    resolved = resolve_profile_job(spec(), PROFILES)
    profile = json.loads(json.dumps(PROFILE))
    if change == "argv":
        profile["modes"][0]["argv"].append("--fast")
    elif change == "image":
        profile["imageSourceRef"] = "moonmind-python-tests"
    else:
        profile["profileRef"] = "renamed"
    with pytest.raises(ValueError):
        validate_profile_launch(resolved, settings(profile).workload_profiles)


def test_worker_refuses_an_unresolved_profile_request():
    with pytest.raises(ValueError, match="not resolved"):
        validate_profile_launch(spec(), PROFILES)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(imageSourceRef="undeclared"),
        lambda p: p.update(caches=[{"cacheRef": "missing", "target": "/opt/rules"}]),
        lambda p: p.update(caches=[{"cacheRef": "scanner-rules", "target": "/x"}]),
        lambda p: p.update(networkMode="bridge"),
        lambda p: p["modes"][0]["argv"].append("{undeclared}"),
        lambda p: p.update(unexpected=True),
    ],
)
def test_settings_reject_unsafe_profiles(mutate):
    profile = json.loads(json.dumps(PROFILE))
    mutate(profile)
    with pytest.raises(ContainerBackendConfigError):
        settings(profile)


def test_settings_reject_profiles_on_writable_shared_caches():
    with pytest.raises(ContainerBackendConfigError):
        settings(cache_read_only=False)


def scoped(scope=None, **selection):
    return spec(
        selection={"assessmentRef": "assessment-1", **selection},
        **({"inputs": scope} if scope else {}),
    )


def test_scope_admits_profile_jobs_inside_targets():
    admitted = AdmittedAssessmentScope.model_validate(SCOPE)
    job = resolve_profile_job(scoped(), PROFILES)
    assert selection_within_scope(job, admitted)
    resolved_job_within_scope(job, admitted, PROFILES)
    artifact = scoped([{"name": "source", "artifactRef": "art_sbom1"}])
    assert selection_within_scope(artifact, admitted)


@pytest.mark.parametrize(
    "job",
    [
        lambda: scoped(assessmentRef="assessment-2"),
        spec,
        lambda: scoped(configurationExceptions=["inline-suppressions"]),
        lambda: scoped([{"name": "source", "workspacePath": "."}]),
        # Capturing app would include the excluded app/vendor subtree.
        lambda: scoped([{"name": "source", "workspacePath": "app"}]),
        lambda: scoped([{"name": "source", "workspacePath": "lib"}]),
        lambda: scoped([{"name": "source", "workspacePath": "app/vendor/x"}]),
        lambda: scoped([{"name": "source", "artifactRef": "art_other"}]),
        lambda: ContainerJobSpec.model_validate(
            {
                "image": "alpine",
                "workspaceRef": {"kind": "sandbox", "workspaceId": "sandbox-1"},
                "resources": {"cpuMillis": 100, "memoryMiB": 64},
            }
        ),
    ],
)
def test_scope_refuses_requests_outside_the_assessment(job):
    assert not selection_within_scope(
        job(), AdmittedAssessmentScope.model_validate(SCOPE)
    )


def test_scope_refuses_unadmitted_action_classes_and_budgets():
    admitted = AdmittedAssessmentScope.model_validate(SCOPE)
    probe = resolve_profile_job(
        spec(
            {"target": "source"},
            mode="probe",
            selection={"assessmentRef": "assessment-1"},
        ),
        PROFILES,
    )
    with pytest.raises(PermissionError, match="action class"):
        resolved_job_within_scope(probe, admitted, PROFILES)
    tight = AdmittedAssessmentScope.model_validate({**SCOPE, "maxJobSeconds": 60})
    with pytest.raises(PermissionError, match="budget"):
        resolved_job_within_scope(
            resolve_profile_job(scoped(), PROFILES), tight, PROFILES
        )


def test_scope_refuses_network_not_admitted():
    profile = {
        **json.loads(json.dumps(PROFILE)),
        "networkMode": "bridge",
        "egressPolicyRef": "feeds",
    }
    profiles = settings(profile).workload_profiles
    job = resolve_profile_job(scoped(), profiles)
    with pytest.raises(PermissionError, match="network"):
        resolved_job_within_scope(
            job, AdmittedAssessmentScope.model_validate(SCOPE), profiles
        )
    resolved_job_within_scope(
        job,
        AdmittedAssessmentScope.model_validate({**SCOPE, "networkRefs": ["feeds"]}),
        profiles,
    )


def mint(**kwargs):
    from moonmind.schemas.container_job_models import OwnerIdentity

    return mint_container_job_session_capability(
        secret="scope-secret",
        owner=OwnerIdentity(principalId="step-1", principalType="service"),
        agent_run_id="step-1",
        workflow_id="workflow-1",
        session_id="lease-1",
        runtime_id="codex-native",
        lifetime_seconds=300,
        **kwargs,
    )


def test_scoped_capability_round_trips_its_signed_scope():
    admitted = AdmittedAssessmentScope.model_validate(SCOPE)
    capability = verify_container_job_session_capability(
        mint(assessment_scope=admitted), secret="scope-secret"
    )
    assert capability.assessment_scope == admitted
    assert (
        verify_container_job_session_capability(
            mint(), secret="scope-secret"
        ).assessment_scope
        is None
    )


def test_scoped_capability_rejects_tampered_or_downgraded_scope():
    import base64
    import hashlib
    import hmac

    token = mint(assessment_scope=AdmittedAssessmentScope.model_validate(SCOPE))
    encoded, _, _ = token.partition(".")
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))

    def resign(changed):
        body = (
            base64.urlsafe_b64encode(
                json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
            )
            .rstrip(b"=")
            .decode()
        )
        signature = hmac.new(b"scope-secret", body.encode(), hashlib.sha256).digest()
        return body + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    # A version-3 token cannot smuggle a scope an older verifier would ignore,
    # and a version-4 token without a valid scope is not unrestricted.
    for changed in (
        {**payload, "v": 3},
        {key: value for key, value in payload.items() if key != "assessmentScope"},
        {**payload, "assessmentScope": {"assessmentRef": "assessment-1"}},
    ):
        with pytest.raises(ContainerJobCapabilityError):
            verify_container_job_session_capability(
                resign(changed), secret="scope-secret"
            )
    altered = {
        **payload,
        "assessmentScope": {**payload["assessmentScope"], "maxJobs": 999},
    }
    with pytest.raises(ContainerJobCapabilityError):
        verify_container_job_session_capability(
            resign(altered).partition(".")[0] + "." + token.partition(".")[2],
            secret="scope-secret",
        )


def test_policy_scope_is_absent_valid_or_fails_closed():
    assert assessment_scope_from_policy({}) is None
    assert assessment_scope_from_policy(None) is None
    assert assessment_scope_from_policy({"assessmentScope": SCOPE}) == (
        AdmittedAssessmentScope.model_validate(SCOPE)
    )
    for invalid in (None, {}, {**SCOPE, "profileRefs": []}, {**SCOPE, "extra": 1}):
        with pytest.raises(ContainerJobCapabilityError):
            assessment_scope_from_policy({"assessmentScope": invalid})
