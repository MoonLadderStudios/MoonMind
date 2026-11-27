"""Live hosts as an admitted endpoint target class (#5440)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobSpec,
    ContainerJobSubmitRequest,
    OwnerIdentity,
    WorkloadCredentialAuthorization,
)
from moonmind.schemas.repository_scanner_models import (
    EndpointAssessment,
    host_key_fingerprint,
)
from moonmind.security.endpoint_assessments import (
    EndpointExecution,
    build_endpoint_assessment_job,
    parse_results,
    summarize,
    verify_summary,
)
from moonmind.security.workload_credentials import (
    authorize_workload_credentials,
    launch_authorizations,
)
from moonmind.security.workload_profiles import (
    AdmittedAssessmentScope,
    WorkloadProfile,
    resolve_profile_job,
    resolved_job_within_scope,
    selection_within_scope,
    validate_profile_launch,
)

NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
OWNER = OwnerIdentity(principalId="operator", principalType="user")
JOB = "container-job:" + "c" * 32
PROFILE_ID = "xccdf_org.ssgproject.content_profile_standard"
RULE = "xccdf_org.ssgproject.content_rule_sshd_disable_root_login"
SKIPPED = "xccdf_org.ssgproject.content_rule_package_aide_installed"
LIMITS = {
    "maxConnections": 8,
    "maxBytes": 256 * 1024 * 1024,
    "maxConnectionsPerSecond": 2,
    "maxConcurrentConnections": 1,
    "maxDurationSeconds": 900,
}


def ssh_key(kind: str = "ssh-ed25519", seed: int = 1) -> str:
    body = bytes((seed + index) % 256 for index in range(32))
    blob = (
        len(kind).to_bytes(4, "big")
        + kind.encode()
        + len(body).to_bytes(4, "big")
        + body
    )
    return f"{kind} {base64.b64encode(blob).decode()}"


KEY = ssh_key()
OTHER_KEY = ssh_key(seed=7)

PROFILE = WorkloadProfile.model_validate(
    {
        "profileRef": "endpoint-host",
        "imageSourceRef": "repository-security",
        "entrypoint": ["python"],
        "modes": [
            {
                "name": "evaluate",
                "actionClass": "endpoint-evaluation",
                "argv": ["-I", "-m", "moonmind.security.endpoint_assessment_runner"],
            }
        ],
        "networkMode": "bridge",
        "egressPolicyRef": "native-lab",
        "nativeNetwork": {
            "targets": [
                {
                    "hosts": ["93.184.216.32/30"],
                    "ports": [22, 2222],
                    "purpose": "live-target",
                }
            ],
            "limits": {**LIMITS, "maxConnections": 64, "maxDurationSeconds": 3600},
        },
        "credentials": [
            {"name": "host", "purpose": "host_inspection", "delivery": "file"}
        ],
        "outputs": [
            {"name": "results", "relativePath": "endpoint-assessment/results.xml"},
            {"name": "execution", "relativePath": "endpoint-assessment/execution.json"},
            {"name": "summary", "relativePath": "endpoint-assessment/summary.json"},
        ],
        "maxCpuMillis": 2000,
        "maxMemoryMiB": 2048,
        "maxTimeoutSeconds": 3600,
    }
)


def assessment(**overrides) -> dict:
    host = {
        "address": "93.184.216.34",
        "port": 22,
        "account": "inspector",
        "hostKey": KEY,
        "limits": LIMITS,
        **overrides.pop("host", {}),
    }
    return {
        "target": "host",
        "asset": "web-01",
        "datastream": "ssg-debian12-ds.xml",
        "profile": PROFILE_ID,
        "ruleIds": [RULE],
        "skipRuleIds": [SKIPPED],
        "assessmentRef": "lab",
        "credentialRef": "db://host-key",
        "egressPolicyRef": "native-lab",
        "timeoutSeconds": 900,
        "host": host,
        **overrides,
    }


def grant(**overrides) -> dict:
    return {
        "ref": "db://host-key",
        "purpose": "host_inspection",
        "account": "inspector",
        "profileRefs": ["endpoint-host"],
        "expiresAt": (NOW + timedelta(hours=4)).isoformat(),
        "revision": 2,
        **overrides,
    }


def host_target(**overrides) -> dict:
    return {
        "endpointHost": {
            "address": "93.184.216.34",
            "port": 22,
            "osFamily": "linux",
            "hostKey": KEY,
            "profiles": [{"datastream": "ssg-debian12-ds.xml", "profile": PROFILE_ID}],
            "excludedRuleIds": [SKIPPED],
            **overrides,
        }
    }


def scope(*targets, grants=None, native_jobs=4, **overrides) -> AdmittedAssessmentScope:
    # Native connections, bytes, rate and concurrency are reserved per job.
    return AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "lab",
            "profileRefs": ["endpoint-host"],
            "actionClasses": ["endpoint-evaluation"],
            "targets": [
                {
                    "nativeNetwork": {
                        "targets": [
                            {
                                "hosts": ["93.184.216.34"],
                                "ports": [22],
                                "purpose": "live-target",
                            }
                        ],
                        "limits": {
                            **{
                                key: value * native_jobs
                                for key, value in LIMITS.items()
                            },
                            "maxDurationSeconds": LIMITS["maxDurationSeconds"],
                        },
                    }
                },
                *(targets or [host_target()]),
            ],
            "networkRefs": ["native-lab"],
            "maxJobs": 4,
            "maxJobSeconds": 1200,
            "maxCpuMillis": 2000,
            "maxMemoryMiB": 2048,
            "evidenceIntent": "endpoint configuration",
            "grants": grants if grants is not None else [grant()],
            **overrides,
        }
    )


def job(**overrides) -> ContainerJobSpec:
    model = EndpointAssessment.model_validate(assessment(**overrides))
    return ContainerJobSpec.model_validate(build_endpoint_assessment_job(model))


def resolved(**overrides) -> ContainerJobSpec:
    return resolve_profile_job(job(**overrides), (PROFILE,))


def test_authorized_host_admits_a_typed_endpoint_job_on_the_native_path():
    spec = resolved()
    admitted = scope()
    resolved_job_within_scope(spec, admitted, (PROFILE,))
    assert selection_within_scope(spec, admitted)
    validate_profile_launch(spec, (PROFILE,))

    # The profile's native egress and runner; the job reaches one endpoint.
    assert spec.network_mode == "bridge"
    assert spec.egress_policy_ref == "native-lab"
    assert spec.image_source_ref == "repository-security"
    assert spec.input_attachments == []
    native = spec.workload_profile.native_network
    assert [t.endpoints() for t in native.targets] == [(("93.184.216.34", 22),)]
    assert spec.command[:3] == [
        "-I",
        "-m",
        "moonmind.security.endpoint_assessment_runner",
    ]
    assert spec.command[-1] == "/outputs/endpoint-assessment"
    assert [o.relative_path for o in spec.outputs] == [
        "endpoint-assessment/results.xml",
        "endpoint-assessment/execution.json",
        "endpoint-assessment/summary.json",
    ]
    # Only the reference travels; the credential is a delivered file.
    assert spec.environment == []
    decisions = authorize_workload_credentials(
        spec, admitted, (PROFILE,), principal="user:operator", now=NOW
    )
    assert [(d["slot"], d["purpose"], d["credentialRef"]) for d in decisions] == [
        ("host", "host_inspection", "db://host-key")
    ]
    request = ContainerJobActivityRequest(
        jobId=JOB,
        owner=OWNER,
        ownershipToken=f"{JOB}:v1",
        request=ContainerJobSubmitRequest(
            idempotencyKey="k", source={"source": "mcp"}, spec=spec
        ),
        workloadCredentials=tuple(
            WorkloadCredentialAuthorization.model_validate({**d, "jobId": JOB})
            for d in decisions
        ),
    )
    ((slot, decision),) = launch_authorizations(request, (PROFILE,), now=NOW)
    assert (slot.name, slot.delivery, decision.credential_revision) == (
        "host",
        "file",
        2,
    )
    with pytest.raises(TimeoutError):
        launch_authorizations(request, (PROFILE,), now=NOW + timedelta(hours=5))


@pytest.mark.parametrize(
    "overrides,targets,message",
    [
        ({"host": {"address": "93.184.216.35"}}, None, "is not an admitted endpoint"),
        ({}, [host_target(address="93.184.216.35")], "is not an admitted endpoint"),
        ({}, [host_target(port=2222)], "port 22 is not admitted"),
        ({}, [host_target(hostKey=OTHER_KEY)], "host identity differs"),
        (
            {"profile": "xccdf_org.ssgproject.content_profile_anssi_np_nt28_minimal"},
            None,
            "is not admitted for host",
        ),
        ({"datastream": "ssg-ubuntu2404-ds.xml"}, None, "is not admitted for host"),
        ({"skipRuleIds": []}, None, "omits a rule the authorization excludes"),
        ({"host": {"elevated": True}}, None, "elevated evaluation is not admitted"),
        ({"assessmentRef": "other"}, None, "belongs to another assessment"),
        ({"egressPolicyRef": "native-other"}, None, None),
    ],
)
def test_admission_refuses_what_the_authorization_does_not_name(
    overrides, targets, message
):
    try:
        spec = resolved(**overrides)
    except ValueError:
        # Rendering against the deployment profile already refuses it.
        assert message is None
        return
    admitted = scope(*(targets or []))
    with pytest.raises(PermissionError) as exc:
        resolved_job_within_scope(spec, admitted, (PROFILE,))
    if message:
        assert message in str(exc.value)
    assert not selection_within_scope(spec, admitted)


def test_elevated_rights_need_explicit_authorization():
    spec = resolved(host={"elevated": True})
    resolved_job_within_scope(spec, scope(host_target(elevated=True)), (PROFILE,))


def test_action_and_profile_outside_the_scope_are_refused():
    spec = resolved()
    with pytest.raises(PermissionError, match="action class is not admitted"):
        resolved_job_within_scope(
            spec, scope(actionClasses=["service-inventory"]), (PROFILE,)
        )
    assert not selection_within_scope(spec, scope(profileRefs=["cloud-posture"]))


@pytest.mark.parametrize(
    "grants,message",
    [
        ([], "not admitted for this assessment"),
        ([grant(purpose="application_auth")], "not admitted for this purpose"),
        (
            [
                grant(
                    purpose="cloud_inventory",
                    account="123456789012",
                    roleArn="arn:aws:iam::123456789012:role/audit",
                )
            ],
            "not admitted for this purpose",
        ),
        ([grant(account="root")], "not admitted for this account"),
        ([grant(profileRefs=["service-inventory"])], "not admitted for this profile"),
        ([grant(expiresAt=(NOW - timedelta(minutes=1)).isoformat())], "expired"),
        (
            [grant(expiresAt=(NOW + timedelta(minutes=5)).isoformat())],
            "expires before the job budget ends",
        ),
    ],
)
def test_only_a_live_host_inspection_grant_fills_the_host_slot(grants, message):
    with pytest.raises(PermissionError, match=message):
        authorize_workload_credentials(
            resolved(), scope(grants=grants), (PROFILE,), principal="user:o", now=NOW
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"remediate": True},
        {"fixScript": "art_fix"},
        {"command": ["rm", "-rf", "/"]},
        {"args": ["--remediate"]},
    ],
)
def test_contract_has_no_remediation_or_command_input(extra):
    with pytest.raises(ValidationError):
        EndpointAssessment.model_validate({**assessment(), **extra})
    with pytest.raises(ValidationError):
        EndpointAssessment.model_validate(
            assessment(host={"command": "id"} if "command" in extra else extra)
        )


def test_profile_job_cannot_add_arguments_commands_or_a_second_endpoint():
    raw = build_endpoint_assessment_job(EndpointAssessment.model_validate(assessment()))
    for change in (
        {"arguments": {"script": "x"}},
        {"configurationExceptions": ["anything"]},
        {
            "nativeNetwork": {
                **raw["workloadProfile"]["nativeNetwork"],
                "targets": [
                    {
                        "hosts": ["93.184.216.34"],
                        "ports": [22, 2222],
                        "purpose": "live-target",
                    }
                ],
            }
        },
        {"credentials": []},
    ):
        mutated = json.loads(json.dumps(raw))
        mutated["workloadProfile"].update(change)
        with pytest.raises(ValueError):
            resolve_profile_job(ContainerJobSpec.model_validate(mutated), (PROFILE,))
    mutated = json.loads(json.dumps(raw))
    mutated["command"] = ["sh", "-c", "id"]
    with pytest.raises(ValueError):
        resolve_profile_job(ContainerJobSpec.model_validate(mutated), (PROFILE,))
    # The offline workload never carries a live host.
    with pytest.raises(ValidationError):
        ContainerJobSpec.model_validate(
            {
                "imageSourceRef": "repository-security",
                "endpointAssessment": assessment(),
                "inputAttachments": [],
                "resources": {"cpuMillis": 1000, "memoryMiB": 1024},
            }
        )


@pytest.mark.parametrize(
    "change",
    [
        {"inputArtifactRef": "art_x"},
        {"tailoringArtifactRef": "art_t"},
        {"credentialRef": None},
        {"host": None, "target": "host"},
        {"skipRuleIds": [RULE]},
        {"host": {"address": "93.184.216.0/30"}},
        {"host": {"address": "10.0.0.5"}},
        {"host": {"hostKey": "ssh-ed25519 notbase64"}},
        {"host": {"hostKey": ssh_key("ssh-rsa").replace("ssh-rsa", "ssh-ed25519", 1)}},
        {"credentialRef": "plain-secret-value"},
    ],
)
def test_live_contract_fails_closed(change):
    value = assessment()
    for key, item in change.items():
        if key == "host" and isinstance(item, dict):
            value["host"] = {**value["host"], **item}
        elif item is None:
            value.pop(key, None)
        else:
            value[key] = item
    with pytest.raises(ValidationError):
        EndpointAssessment.model_validate(value)


def test_private_fixture_hosts_name_their_labeled_route():
    model = EndpointAssessment.model_validate(
        assessment(
            host={
                "address": "10.0.0.5",
                "targetNetworkRef": "fixture-net",
                "targetContainerRef": "fixture-host",
            }
        )
    )
    (target,) = model.host.native_selection().targets
    assert (target.target_network_ref, target.target_container_ref) == (
        "fixture-net",
        "fixture-host",
    )


def test_authorization_host_target_is_explicit():
    with pytest.raises(ValidationError):
        scope(host_target(osFamily="windows"))
    with pytest.raises(ValidationError):
        scope(host_target(address="93.184.216.0/30"))
    with pytest.raises(ValidationError):
        scope(host_target(profiles=[]))
    with pytest.raises(ValidationError):
        scope(host_target(), host_target())
    dumped = scope().model_dump(mode="json", by_alias=True, exclude_none=True)
    assert dumped["targets"][1]["endpointHost"]["hostKey"] == KEY
    assert "endpointHost" not in dumped["targets"][0]


_ARF = f"""<?xml version="1.0"?>
<TestResult xmlns="http://checklists.nist.gov/xccdf/1.2"
  id="xccdf_org.moonmind_testresult_standard"
  start-time="2026-11-27T12:00:00+00:00" end-time="2026-11-27T12:01:00+00:00">
  <benchmark id="xccdf_org.ssgproject.content_benchmark_DEBIAN-12"/>
  <profile idref="{PROFILE_ID}"/>
  <rule-result idref="{RULE}"><result>fail</result></rule-result>
  <rule-result idref="{SKIPPED}"><result>notselected</result></rule-result>
</TestResult>""".encode()


def test_live_summary_keeps_the_offline_shape_with_a_live_subject():
    model = EndpointAssessment.model_validate(assessment())
    execution = EndpointExecution(
        target="host",
        version="1.3.7",
        exitCode=2,
        state="succeeded",
        contentFile="ssg-debian12-ds.xml",
        contentDigest="a" * 64,
        contentRelease="0.1.78",
    )
    native = parse_results(_ARF, model.max_report_bytes)
    summary = summarize(model, execution, native, native_ref="art_results")
    assert summary.schema_version == "endpoint-assessment/v1"
    assert summary.coverage == "complete", summary.limitations
    assert summary.subject.kind == "host"
    assert summary.subject.observation == "live-host"
    assert summary.subject.identity.startswith("ssh-host@sha256:")
    assert summary.subject.address == "93.184.216.34:22"
    assert summary.subject.host_key_fingerprint == host_key_fingerprint(KEY)
    assert summary.observed_at == datetime(2026, 11, 27, 12, 1, tzinfo=timezone.utc)
    assert [(r.rule_id, r.state) for r in summary.rules] == [
        (RULE, "failed"),
        (SKIPPED, "not_assessed"),
    ]
    assert verify_summary(summary, _ARF) == summary
    # The credential reference is admission data; no value exists to leak.
    assert "db://host-key" in summary.model_dump_json(by_alias=True)

    # A host that never produced results has no subject identity.
    failed = EndpointExecution(
        target="host",
        version="unavailable",
        state="failed",
        limitations=["host_identity_unverified"],
    )
    stopped = summarize(model, failed, None, native_ref=None)
    assert stopped.subject is None
    assert stopped.coverage == "partial"
    assert {"host_identity_unverified", "target_identity_unavailable"} <= set(
        stopped.limitations
    )


def test_offline_summary_shape_is_unchanged():
    model = EndpointAssessment.model_validate(
        {
            "target": "rootfs",
            "asset": "web-01",
            "inputArtifactRef": "art_x",
            "datastream": "ssg-debian12-ds.xml",
            "profile": PROFILE_ID,
        }
    )
    execution = EndpointExecution(
        target="rootfs",
        version="1.3.7",
        state="succeeded",
        inputDigest="b" * 64,
        contentFile="ssg-debian12-ds.xml",
        contentDigest="a" * 64,
    )
    summary = summarize(
        model, execution, parse_results(_ARF, 1 << 20), native_ref="art_results"
    )
    subject = summary.model_dump(mode="json", by_alias=True)["subject"]
    assert set(subject) == {
        "asset",
        "kind",
        "identity",
        "inputDigest",
        "os",
        "declaredTarget",
        "observation",
        "statement",
    }
    assert summary.observed_at is None
