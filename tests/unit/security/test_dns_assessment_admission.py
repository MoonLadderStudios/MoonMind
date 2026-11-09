"""DNS question authority stays distinct from resolver connectivity."""

import copy
import json

import pytest

from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobSpec,
    ContainerJobSubmitRequest,
)
from moonmind.security.dns_assessments import decode_dns_query_plan
from moonmind.security.egress_policy import EGRESS_POLICIES_ENV_KEY
from moonmind.security.workload_profiles import (
    WORKLOAD_PROFILES_ENV_KEY,
    AdmittedAssessmentScope,
    WorkloadProfile,
    resolve_profile_job,
    resolved_job_within_scope,
    selection_within_scope,
    validate_profile_launch,
)

ASSESSMENT = {
    "questions": [{"name": "app.example.test", "recordType": "CNAME"}],
    "allowedFollowups": [{"name": "target.example.test", "recordType": "A"}],
    "checks": [{"check": "cname", "subject": "app.example.test"}],
    "maxAttempts": 4,
    "timeoutSeconds": 30,
    "queryTimeoutSeconds": 5,
    "queriesPerSecond": 2,
}
PROFILE = {
    "profileRef": "dns-observation",
    "imageSourceRef": "repository-security",
    "entrypoint": ["python"],
    "modes": [
        {
            "name": "query",
            "actionClass": "dns-observation",
            "argv": ["-I", "-m", "moonmind.security.dns_assessment_runner"],
        }
    ],
    "dnsResolver": {"endpoint": "https://resolver.example.test/dns-query"},
    "networkMode": "bridge",
    "egressPolicyRef": "resolver-egress",
    "outputs": [
        {"name": "execution", "relativePath": "dns-assessment/execution.json"},
        {"name": "summary", "relativePath": "dns-assessment/summary.json"},
    ],
    "maxCpuMillis": 1000,
    "maxMemoryMiB": 512,
    "maxTimeoutSeconds": 60,
}
SCOPE = {
    "assessmentRef": "dns-1",
    "profileRefs": ["dns-observation"],
    "actionClasses": ["dns-observation"],
    "targets": [{"dnsAssessment": ASSESSMENT}],
    "networkRefs": ["resolver-egress"],
    "maxJobs": 2,
    "maxJobSeconds": 60,
    "maxRequestsPerSecond": 2,
    "evidenceIntent": "Bounded resolver-view DNS observations",
}
POLICY = {
    "policyRef": "resolver-egress",
    "allowedWorkflowIds": ["workflow-1"],
    "allowedImageSourceRefs": ["repository-security"],
    "destinations": [
        {
            "host": "resolver.example.test",
            "protocol": "https",
            "port": 443,
            "purpose": "provider-api",
        }
    ],
}


def spec(**fields):
    return ContainerJobSpec.model_validate(
        {
            "workloadProfile": {
                "profileRef": "dns-observation",
                "mode": "query",
                "assessmentRef": "dns-1",
                "dnsAssessment": copy.deepcopy(ASSESSMENT),
            },
            "workspaceRef": {"kind": "sandbox", "workspaceId": "sandbox-1"},
            "inputAttachments": [],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            "timeoutSeconds": 30,
            **fields,
        }
    )


def settings(policy=None, profile=None):
    return resolve_container_backend_settings(
        {
            IMAGE_SOURCES_ENV_KEY: json.dumps(
                [
                    {
                        "sourceRef": "repository-security",
                        "image": "ghcr.io/example/scanner:latest",
                    }
                ]
            ),
            WORKLOAD_PROFILES_ENV_KEY: json.dumps([profile or PROFILE]),
            EGRESS_POLICIES_ENV_KEY: json.dumps([policy or POLICY]),
        }
    )


def request(resolved):
    return ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "dns-job-1",
            "spec": resolved.model_dump(mode="json", by_alias=True),
            "source": {
                "source": "workflow",
                "workflowId": "workflow-1",
                "runId": "run-1",
                "stepId": "dns",
            },
        }
    )


def test_fixed_dns_job_renders_without_workspace_capture():
    config = settings()
    resolved = resolve_profile_job(spec(), config.workload_profiles)
    plan = decode_dns_query_plan(resolved.command[3:])
    assert plan.resolver.endpoint == PROFILE["dnsResolver"]["endpoint"]
    assert plan.assessment.questions[0].name == "app.example.test"
    assert resolved.input_attachments == []
    assert resolved.entrypoint == ["python"]
    assert resolved.environment == []
    scope = AdmittedAssessmentScope.model_validate(SCOPE)
    assert selection_within_scope(resolved, scope)
    resolved_job_within_scope(resolved, scope, config.workload_profiles)
    validate_profile_launch(resolved, config.workload_profiles)
    assert config.authorize_egress(request(resolved)).policy_ref == "resolver-egress"


@pytest.mark.parametrize(
    "change",
    [
        {
            "questions": [{"name": "neighbor.example.test", "recordType": "CNAME"}],
            "checks": [],
        },
        {
            "questions": [{"name": "target.example.test", "recordType": "A"}],
            "allowedFollowups": [],
            "checks": [],
        },
        {"allowedFollowups": [{"name": "other.example.test", "recordType": "A"}]},
        {"maxAttempts": 5},
        {"timeoutSeconds": 31},
        {"queryTimeoutSeconds": 6},
        {"queriesPerSecond": 3},
        {"maxFollowupDepth": 5},
    ],
)
def test_scope_cannot_expand_questions_followups_or_budgets(change):
    candidate = spec().model_dump(mode="json", by_alias=True)
    candidate["workloadProfile"]["dnsAssessment"].update(change)
    selected = ContainerJobSpec.model_validate(candidate)
    assert not selection_within_scope(
        selected, AdmittedAssessmentScope.model_validate(SCOPE)
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("assessmentRef", "other-assessment"),
        ("arguments", {"target": "neighbor"}),
        ("configurationExceptions", ["custom-args"]),
    ],
)
def test_dns_selection_cannot_borrow_generic_profile_options(key, value):
    candidate = spec().model_dump(mode="json", by_alias=True)
    candidate["workloadProfile"][key] = value
    selected = ContainerJobSpec.model_validate(candidate)
    if key == "assessmentRef":
        assert not selection_within_scope(
            selected, AdmittedAssessmentScope.model_validate(SCOPE)
        )
    else:
        with pytest.raises(ValueError):
            resolve_profile_job(selected, settings().workload_profiles)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(entrypoint=["sh"]),
        lambda p: p.update(
            environment={"ARBITRARY_ENDPOINT": "https://other.example.test"}
        ),
        lambda p: p["modes"][0].update(argv=["-I", "-m", "other.runner"]),
        lambda p: p["modes"][0].update(actionClass="live-probe"),
        lambda p: p["outputs"].append(
            {"name": "private", "relativePath": "private.json"}
        ),
    ],
)
def test_dns_profile_has_one_fixed_invocation(mutation):
    profile = copy.deepcopy(PROFILE)
    mutation(profile)
    with pytest.raises(ValueError):
        resolve_profile_job(spec(), (WorkloadProfile.model_validate(profile),))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["destinations"][0].update(host="app.example.test"),
        lambda p: p["destinations"][0].update(protocol="http"),
        lambda p: p["destinations"][0].update(port=8443),
        lambda p: p["destinations"].append(
            {
                "host": "neighbor.example.test",
                "protocol": "https",
                "port": 443,
                "purpose": "provider-api",
            }
        ),
    ],
)
def test_resolver_policy_cannot_broaden_or_confuse_question_authority(mutation):
    policy = copy.deepcopy(POLICY)
    mutation(policy)
    config = settings(policy=policy)
    resolved = resolve_profile_job(spec(), config.workload_profiles)
    candidate = request(resolved)
    with pytest.raises(ValueError, match="DNS egress"):
        config.authorize_egress(candidate)


def test_resolver_change_invalidates_persisted_concrete_job():
    resolved = resolve_profile_job(spec(), settings().workload_profiles)
    profile = copy.deepcopy(PROFILE)
    profile["dnsResolver"]["endpoint"] = "https://other.example.test/dns-query"
    with pytest.raises(ValueError):
        validate_profile_launch(resolved, settings(profile=profile).workload_profiles)


def test_dns_job_cannot_mount_a_workspace_or_disable_scope():
    with pytest.raises(ValueError):
        resolve_profile_job(
            spec(inputAttachments=[{"name": "source", "workspacePath": "."}]),
            settings().workload_profiles,
        )
    body = spec().model_dump(mode="json", by_alias=True)
    body["workloadProfile"]["assessmentRef"] = None
    with pytest.raises(ValueError):
        resolve_profile_job(
            ContainerJobSpec.model_validate(body), settings().workload_profiles
        )
