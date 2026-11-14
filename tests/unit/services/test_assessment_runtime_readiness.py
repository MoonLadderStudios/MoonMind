import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services import assessment_runtime_readiness as readiness
from tests.unit.services.test_repository_scanner_readiness import configured


def parameters():
    selection = {"scanner": "gitleaks", "outputDirectory": "reports"}
    return {
        "workflow": {
            "steps": [
                {
                    "skill": {
                        "id": "security-review",
                        "args": {"assessment": selection},
                    },
                    "annotations": {
                        "securityAssessment": {
                            "kind": "repository-scan",
                            "phase": "collect",
                            "admission": copy.deepcopy(selection),
                        }
                    },
                }
            ]
        }
    }


@pytest.mark.asyncio
async def test_grants_and_configuration_checked_before_metadata_or_image():
    backend = SimpleNamespace(_settings=configured(), assessment_readiness=AsyncMock())
    session = SimpleNamespace(execute=AsyncMock())
    p = parameters()
    p["workflow"]["steps"][0]["skill"]["args"]["assessment"]["scanner"] = "opengrep"
    result = await readiness.check_assessment_runtime_readiness(
        p,
        owner={"principalId": "operator", "principalType": "user"},
        workflow_id="wf",
        run_id="run",
        backend=backend,
        session=session,
    )
    assert result["status"] == "blocked"
    session.execute.assert_not_awaited()
    backend.assessment_readiness.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_credentials_need_no_metadata_and_no_job_reservation():
    backend = SimpleNamespace(
        _settings=configured(),
        assessment_readiness=AsyncMock(
            return_value={"status": "ready", "diagnostics": [], "checks": []}
        ),
    )
    session = SimpleNamespace(execute=AsyncMock())
    result = await readiness.check_assessment_runtime_readiness(
        parameters(),
        owner={"principalId": "operator", "principalType": "user"},
        workflow_id="wf",
        run_id="run",
        backend=backend,
        session=session,
    )
    assert result["status"] == "ready"
    session.execute.assert_not_awaited()
    spec = backend.assessment_readiness.call_args.args[0].request.spec
    assert spec.workspace_ref is None and spec.network_mode == "none"
    assert not spec.environment and not spec.caches and not spec.input_attachments


@pytest.mark.asyncio
async def test_secret_metadata_never_selects_ciphertext():
    captured = []

    async def execute(query):
        captured.append(str(query))
        return SimpleNamespace(
            first=lambda: SimpleNamespace(status="active", credential_revision=3)
        )

    result = await readiness.credential_metadata(
        SimpleNamespace(execute=execute), "db://example", revision=3
    )
    assert result == "ready"
    assert "ciphertext" not in captured[0]
    assert "credential_revision" in captured[0]
    assert (
        await readiness.credential_metadata(
            SimpleNamespace(execute=execute), "db://example", revision=2
        )
        == "blocked"
    )


@pytest.mark.asyncio
async def test_remote_credential_backend_does_not_run_or_probe():
    session = SimpleNamespace(execute=AsyncMock())
    assert (
        await readiness.credential_metadata(session, "exec://provider-token")
        == "unverified"
    )
    session.execute.assert_not_awaited()


def cloud_parameters():
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from moonmind.config.container_backend_settings import RegistryImageSource
    from tests.unit.security import test_cloud_assessment as cloud

    settings = replace(
        configured(),
        image_sources=(
            RegistryImageSource("cloud-posture", "registry.example/cloud:fixture"),
        ),
        egress_policies=(cloud.policy(("sts.amazonaws.com", "provider-api")),),
    )
    selection = cloud.admission(credentialRef="db://cloud-audit").model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    scope = cloud.scope().model_dump(mode="json", by_alias=True)
    scope["grants"][0].update(
        ref="db://cloud-audit",
        expiresAt=(datetime.now(UTC) + timedelta(hours=2)).isoformat(),
    )
    p = {
        "assessmentScope": scope,
        "workflow": {
            "steps": [
                {
                    "skill": {
                        "id": "security-review",
                        "args": {"assessment": selection},
                    },
                    "annotations": {
                        "securityAssessment": {
                            "kind": "cloud-assessment",
                            "phase": "collect",
                            "admission": copy.deepcopy(selection),
                        }
                    },
                }
            ]
        },
    }
    return p, settings


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["ungranted", "forged_scope", "changed_selection"])
async def test_authored_selections_cannot_probe_secret_existence(monkeypatch, attack):
    from api_service.services import assessment_authorizations as authority
    from moonmind.security.workload_profiles import AdmittedAssessmentScope

    p, settings = cloud_parameters()
    canonical = AdmittedAssessmentScope.model_validate(
        copy.deepcopy(p["assessmentScope"])
    )
    if attack == "ungranted":
        p["assessmentScope"]["grants"] = []
    elif attack == "forged_scope":
        p["assessmentScope"]["maxJobs"] += 1
    else:
        p["workflow"]["steps"][0]["annotations"]["securityAssessment"]["admission"][
            "credentialRef"
        ] = "db://arbitrary-secret"
    monkeypatch.setattr(
        authority, "canonical_assessment_scope", AsyncMock(return_value=canonical)
    )
    backend = SimpleNamespace(_settings=settings, assessment_readiness=AsyncMock())
    session = SimpleNamespace(execute=AsyncMock())
    result = await readiness.check_assessment_runtime_readiness(
        p,
        owner={"principalId": "operator", "principalType": "user"},
        workflow_id="wf",
        run_id="run",
        backend=backend,
        session=session,
    )
    assert result["status"] == "blocked"
    session.execute.assert_not_awaited()
    backend.assessment_readiness.assert_not_awaited()


@pytest.mark.asyncio
async def test_admitted_missing_credential_blocks_before_image_preparation(monkeypatch):
    from api_service.services import assessment_authorizations as authority
    from moonmind.security.workload_profiles import AdmittedAssessmentScope

    p, settings = cloud_parameters()
    canonical = AdmittedAssessmentScope.model_validate(p["assessmentScope"])
    monkeypatch.setattr(
        authority, "canonical_assessment_scope", AsyncMock(return_value=canonical)
    )
    backend = SimpleNamespace(_settings=settings, assessment_readiness=AsyncMock())
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(first=lambda: None))
    )
    result = await readiness.check_assessment_runtime_readiness(
        p,
        owner={"principalId": "operator", "principalType": "user"},
        workflow_id="wf",
        run_id="run",
        backend=backend,
        session=session,
    )
    assert result["diagnostics"][0]["code"] == "credential_unavailable"
    session.execute.assert_awaited_once()
    backend.assessment_readiness.assert_not_awaited()


def probe_payload(selection, checks):
    import hashlib
    import json

    return json.dumps(
        {
            "schemaVersion": "scanner-readiness/v1",
            "status": "ready",
            "selectionDigest": hashlib.sha256(
                json.dumps(
                    {"repositoryScan": selection}, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "checks": checks,
        }
    ).encode()


@pytest.mark.parametrize(
    "checks",
    [
        [],
        [{"component": "trivy", "code": "scanner_available", "status": "ready"}],
        [{"component": "gitleaks", "code": "scanner_available", "status": "ready"}],
    ],
)
def test_ready_requires_selected_scanner_and_rule_evidence(checks):
    selection = {"scanner": "gitleaks", "outputDirectory": "reports"}
    assert (
        readiness.validate_probe_result(
            probe_payload(selection, checks),
            kind="repository-scan",
            selection=selection,
        )["status"]
        == "unavailable"
    )


def test_ready_rejects_other_selection_digest():
    selection = {"scanner": "gitleaks", "outputDirectory": "reports"}
    checks = [
        {"component": "gitleaks", "code": "scanner_available", "status": "ready"},
        {"component": "rules", "code": "scanner_rules_available", "status": "ready"},
    ]
    assert (
        readiness.validate_probe_result(
            probe_payload(selection, checks),
            kind="repository-scan",
            selection={**selection, "outputDirectory": "other"},
        )["status"]
        == "unavailable"
    )


@pytest.mark.parametrize("mandatory", [True, False])
def test_contradictory_freshness_is_not_ready(mandatory):
    selection = {
        "scanner": "trivy",
        "outputDirectory": "reports",
        "databaseCacheRef": "scanner-db",
        "requireFreshFeeds": mandatory,
    }
    checks = [
        {"component": "trivy", "code": "scanner_available", "status": "ready"},
        {"component": "rules", "code": "scanner_rules_available", "status": "ready"},
        {"component": "feeds", "code": "feed_snapshot_available", "status": "ready"},
        {"component": "feeds", "code": "feed_freshness_verified", "status": "ready"},
        {"component": "feeds", "code": "feed_snapshot_stale", "status": "unverified"},
    ]
    assert (
        readiness.validate_probe_result(
            probe_payload(selection, checks),
            kind="repository-scan",
            selection=selection,
        )["status"]
        == "unavailable"
    )


@pytest.mark.asyncio
async def test_activity_uses_temporal_identity_and_sanitizes_errors(monkeypatch):
    from contextlib import asynccontextmanager

    from api_service.db import base
    from moonmind.workflows.temporal import activity_runtime as runtime
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    runtime.validate_activity_catalog_runtime_bindings(build_default_activity_catalog())

    @asynccontextmanager
    async def session_context():
        yield object()

    monkeypatch.setattr(base, "get_async_session_context", session_context)
    monkeypatch.setattr(
        runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id="trusted-workflow", workflow_run_id="trusted-run"
        ),
    )
    checker = AsyncMock(side_effect=RuntimeError("raw secret must not escape"))
    monkeypatch.setattr(readiness, "check_assessment_runtime_readiness", checker)
    activities = runtime.TemporalAgentRuntimeActivities(container_job_backend=object())
    result = await activities.container_job_assessment_readiness(
        {
            "parameters": parameters(),
            "owner": {"principalId": "operator", "principalType": "user"},
            "workflowId": "forged",
        }
    )
    assert result["status"] == "unavailable"
    assert "secret must not escape" not in str(result)
    assert checker.call_args.kwargs["workflow_id"] == "trusted-workflow"
    assert checker.call_args.kwargs["run_id"] == "trusted-run"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "revision", "expected"),
    [
        (None, None, "blocked"),
        ("", None, "blocked"),
        ("  ", None, "blocked"),
        ("present", None, "ready"),
        ("present", 2, "blocked"),
    ],
)
async def test_env_presence_is_nonempty_and_cannot_prove_revision(
    monkeypatch, value, revision, expected
):
    name = "MOONMIND_READINESS_TEST_SECRET"
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    session = SimpleNamespace(execute=AsyncMock())
    assert (
        await readiness.credential_metadata(session, "env://" + name, revision=revision)
        == expected
    )
    session.execute.assert_not_awaited()


def registry_parameters(monkeypatch, reference):
    from dataclasses import replace

    from api_service.services import registry_authorization as registry
    from moonmind.security.egress_policy import ScopedEgressPolicy

    policy = registry.PrivateImageAuthorizationPolicy(
        grants=(
            registry.RegistryCredentialGrant(
                credentialRef=reference,
                registry="registry.example",
                repositories=("private/*",),
                principals=("user:operator",),
            ),
        )
    )
    monkeypatch.setattr(registry, "load_default_authorization_policy", lambda: policy)
    settings = replace(
        configured(),
        egress_policies=(
            ScopedEgressPolicy.model_validate(
                {
                    "policyRef": "registry",
                    "allowedWorkflowIds": ["wf"],
                    "allowedImageSourceRefs": ["repository-security"],
                    "destinations": [
                        {
                            "host": "registry.example",
                            "purpose": "registry-api",
                            "protocol": "https",
                            "port": 443,
                        }
                    ],
                }
            ),
        ),
    )
    selected = {
        "operation": "sbom-generate",
        "target": "registry",
        "imageReference": "registry.example/private/app:stable",
        "egressPolicyRef": "registry",
        "targetCredentialRef": reference,
    }
    p = {
        "workflow": {
            "steps": [
                {
                    "skill": {
                        "id": "security-review",
                        "args": {"assessment": selected},
                    },
                    "annotations": {
                        "securityAssessment": {
                            "kind": "image-assessment",
                            "phase": "collect",
                            "admission": copy.deepcopy(selected),
                        }
                    },
                }
            ]
        }
    }
    return p, settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference", ["exec://op?read?registry-login", "vault://kv/registry#credential"]
)
@pytest.mark.parametrize("value", [None, "", "invalid", "audit:readiness-secret-canary"])
async def test_authorized_external_registry_readiness_before_image_preparation(
    monkeypatch, tmp_path, reference, value
):
    from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
    from moonmind.workflows.temporal.runtime import registry_auth_resolve

    p, settings = registry_parameters(monkeypatch, reference)

    async def resolve(ref, *, field_name):
        assert ref == reference and field_name == "registryCredentialRef"
        if value is None:
            raise ValueError("unavailable readiness-secret-canary")
        return value

    resolver = AsyncMock(side_effect=resolve)
    monkeypatch.setattr(
        registry_auth_resolve, "resolve_managed_api_key_reference", resolver
    )
    runner = AsyncMock()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, settings=settings, command_runner=runner
    )
    backend.assessment_readiness = AsyncMock(
        return_value={"status": "ready", "checks": [], "diagnostics": []}
    )
    session = SimpleNamespace(execute=AsyncMock())
    result = await readiness.check_assessment_runtime_readiness(
        p,
        owner={"principalId": "operator", "principalType": "user"},
        workflow_id="wf",
        run_id="run",
        backend=backend,
        session=session,
    )
    resolver.assert_awaited_once()
    assert "readiness-secret-canary" not in str(result)
    assert reference not in str(result)
    session.execute.assert_not_awaited()
    runner.assert_not_awaited()
    assert not list(tmp_path.iterdir())
    if value == "audit:readiness-secret-canary":
        assert result["status"] == "ready"
        assert {
            "component": "credentialResolution",
            "status": "ready",
            "code": "credential_resolvable",
        } in result["checks"]
        assert {
            "component": "credentialValidity",
            "status": "unverified",
            "code": "not_probed",
        } in result["checks"]
        backend.assessment_readiness.assert_awaited_once()
    else:
        assert result["status"] == "blocked"
        assert result["diagnostics"][0]["code"] == "credential_unavailable"
        backend.assessment_readiness.assert_not_awaited()


@pytest.mark.asyncio
async def test_ungranted_registry_selection_never_resolves_external_secret(monkeypatch):
    p, settings = registry_parameters(monkeypatch, "exec://op?read?registry-login")
    step = p["workflow"]["steps"][0]
    for selection in (
        step["skill"]["args"]["assessment"],
        step["annotations"]["securityAssessment"]["admission"],
    ):
        selection["targetCredentialRef"] = "vault://kv/ungranted#credential"
    backend = SimpleNamespace(
        _settings=settings,
        _resolve_credential=AsyncMock(),
        assessment_readiness=AsyncMock(),
    )
    result = await readiness.check_assessment_runtime_readiness(
        p,
        owner={"principalId": "operator", "principalType": "user"},
        workflow_id="wf",
        run_id="run",
        backend=backend,
        session=SimpleNamespace(execute=AsyncMock()),
    )
    assert result["diagnostics"][0]["code"] == "assessment_authority_unavailable"
    backend._resolve_credential.assert_not_awaited()
    backend.assessment_readiness.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_external_registry_readiness_is_bounded_and_preserves_cancellation(
    monkeypatch, cancel
):
    import asyncio

    p, settings = registry_parameters(monkeypatch, "exec://op?read?registry-login")
    monkeypatch.setattr(readiness, "_REGISTRY_CREDENTIAL_TIMEOUT_SECONDS", 0.01)
    stopped = False

    async def resolve(reference):
        nonlocal stopped
        try:
            if cancel:
                raise asyncio.CancelledError
            await asyncio.Event().wait()
        finally:
            stopped = True

    backend = SimpleNamespace(
        _settings=settings,
        _resolve_credential=AsyncMock(side_effect=resolve),
        assessment_readiness=AsyncMock(),
    )
    check = readiness.check_assessment_runtime_readiness(
        p,
        owner={"principalId": "operator", "principalType": "user"},
        workflow_id="wf",
        run_id="run",
        backend=backend,
        session=SimpleNamespace(execute=AsyncMock()),
    )
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            _ = await check
    else:
        result = await check
        assert result["status"] == "unavailable"
        assert result["diagnostics"][0]["code"] == "credential_resolution_unavailable"
    assert stopped
    backend.assessment_readiness.assert_not_awaited()


@pytest.mark.asyncio
async def test_anonymous_registry_assessment_does_not_require_a_credential(monkeypatch):
    p, settings = registry_parameters(monkeypatch, "exec://op?read?registry-login")
    step = p["workflow"]["steps"][0]
    del step["skill"]["args"]["assessment"]["targetCredentialRef"]
    del step["annotations"]["securityAssessment"]["admission"]["targetCredentialRef"]
    for selection in (
        step["skill"]["args"]["assessment"],
        step["annotations"]["securityAssessment"]["admission"],
    ):
        selection["imageReference"] = "registry.example/public/app:stable"
    backend = SimpleNamespace(
        _settings=settings,
        _resolve_credential=AsyncMock(),
        assessment_readiness=AsyncMock(
            return_value={"status": "ready", "checks": [], "diagnostics": []}
        ),
    )
    result = await readiness.check_assessment_runtime_readiness(
        p,
        owner={"principalId": "operator", "principalType": "user"},
        workflow_id="wf",
        run_id="run",
        backend=backend,
        session=SimpleNamespace(execute=AsyncMock()),
    )
    assert result["status"] == "ready"
    backend._resolve_credential.assert_not_awaited()
    backend.assessment_readiness.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["env://CLOUD_SECRET", "exec://cloud-login"])
async def test_workload_rejects_backends_its_actual_resolver_cannot_use(
    monkeypatch, reference
):
    from api_service.services import assessment_authorizations as authority
    from moonmind.security.workload_profiles import AdmittedAssessmentScope

    p, settings = cloud_parameters()
    step = p["workflow"]["steps"][0]
    step["skill"]["args"]["assessment"]["credentialRef"] = reference
    step["annotations"]["securityAssessment"]["admission"]["credentialRef"] = reference
    p["assessmentScope"]["grants"][0]["ref"] = reference
    monkeypatch.setattr(
        authority,
        "canonical_assessment_scope",
        AsyncMock(
            return_value=AdmittedAssessmentScope.model_validate(p["assessmentScope"])
        ),
    )
    metadata = AsyncMock()
    monkeypatch.setattr(readiness, "credential_metadata", metadata)
    backend = SimpleNamespace(_settings=settings, assessment_readiness=AsyncMock())
    result = await readiness.check_assessment_runtime_readiness(
        p,
        owner={"principalId": "operator", "principalType": "user"},
        workflow_id="wf",
        run_id="run",
        backend=backend,
        session=SimpleNamespace(execute=AsyncMock()),
    )
    assert result["diagnostics"][0]["code"] == "credential_backend_unavailable"
    metadata.assert_not_awaited()
    backend.assessment_readiness.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_heartbeat_cancellation_stops_readiness_work(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager

    from api_service.db import base
    from moonmind.workflows.temporal import activity_runtime as runtime
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    route = build_default_activity_catalog().resolve_activity(
        "container_job.assessment_readiness"
    )
    assert route.timeouts.heartbeat_timeout_seconds == 30

    @asynccontextmanager
    async def session_context():
        yield object()

    monkeypatch.setattr(base, "get_async_session_context", session_context)
    monkeypatch.setattr(
        runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(workflow_id="wf", workflow_run_id="run"),
    )
    monkeypatch.setattr(runtime.temporal_activity, "in_activity", lambda: True)
    beats = []

    def heartbeat(value):
        beats.append(value)
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime.temporal_activity, "heartbeat", heartbeat)
    cancelled = False

    async def check(*args, **kwargs):
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    monkeypatch.setattr(readiness, "check_assessment_runtime_readiness", check)
    original = runtime._await_with_activity_heartbeats

    async def fast(awaitable, **kwargs):
        return await original(awaitable, **{**kwargs, "interval_seconds": 0.001})

    monkeypatch.setattr(runtime, "_await_with_activity_heartbeats", fast)
    with pytest.raises(asyncio.CancelledError):
        await runtime.TemporalAgentRuntimeActivities(
            container_job_backend=object()
        ).container_job_assessment_readiness(
            {
                "parameters": parameters(),
                "owner": {"principalId": "operator", "principalType": "user"},
            }
        )
    assert cancelled and beats == [{"phase": "assessment_readiness"}]
