from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from api_service.db.models import (
    OmnigentHostBindingRecordV2,
    OmnigentHostLeaseRecordV2,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.host_classes import (
    get_host_class,
    get_launch_policy,
)
from moonmind.omnigent.realizers.deployment_adapters import (
    DeploymentGenericHostServices,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
)


class _ArtifactGateway:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.read_ids: list[str] = []

    async def read(self, *, artifact_id: str, **_kwargs):
        self.read_ids.append(artifact_id)
        return SimpleNamespace(artifact_id=artifact_id), self.payloads[artifact_id]

    async def write_json(self, *, name: str, **_kwargs) -> str:
        return "artifact:evidence:" + name


class _MemorySessionFactory:
    def __init__(self) -> None:
        self.records: dict[tuple[type, str], object] = {}

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model: type, identity: str):
        return self.records.get((model, identity))

    def add(self, value: object) -> None:
        identity = getattr(value, "lease_id", None) or getattr(
            value, "binding_id", None
        )
        self.records[(type(value), str(identity))] = value

    async def commit(self) -> None:
        return None


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_generic_host_materializes_the_plan_recorded_skill_artifacts(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_body = b"---\nname: exact-skill\ndescription: exact\n---\n"
    skillset = ResolvedSkillSet(
        snapshot_id="skillset-exact",
        resolved_at=datetime(2026, 8, 22, tzinfo=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="exact-skill",
                content_ref="art-skill-body",
                content_digest=(
                    "sha256:" + hashlib.sha256(skill_body).hexdigest()
                ),
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            )
        ],
    )
    manifest_body = _json_bytes(
        skillset.model_dump(mode="json", exclude_none=True)
    )
    manifest_digest = "sha256:" + hashlib.sha256(manifest_body).hexdigest()
    gateway = _ArtifactGateway(
        {
            "art-skill-manifest": manifest_body,
            "art-skill-body": skill_body,
        }
    )
    monkeypatch.setenv(
        "MOONMIND_ACTIVE_SKILLS_DIR", str(tmp_path / "unrelated-active-skills")
    )
    service = DeploymentGenericHostServices(
        session_factory=None,
        artifact_gateway=gateway,
        credential_materializer=SimpleNamespace(),
        runtime_root=tmp_path / "runtime",
        client=SimpleNamespace(),
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-profile-1",
        correlationId="workflow-1",
        idempotencyKey="step-1",
        resolvedSkillsetRef="art-skill-manifest",
    )
    plan = SimpleNamespace(
        planRef="omnigent-execution-plan:sha256:" + "a" * 64,
        payload=SimpleNamespace(harnessId="opencode-native"),
    )
    authority = {
        "executionPlanRef": plan.planRef,
        "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "b" * 64,
    }
    await service.prepare_realization(
        request=request, plan=plan, authority=authority
    )

    result = await service.materialize(
        {
            "resolvedSkillSetRef": "artifact:art-skill-manifest",
            "resolvedSkillSetDigest": manifest_digest,
            "skillDeliveryRef": "skill-delivery:sha256:" + "c" * 64,
        },
        authority=authority,
    )

    materialized = tmp_path / "runtime" / "skills"
    assert result["path"].startswith(str(materialized))
    assert (
        Path(result["path"]) / "exact-skill" / "SKILL.md"
    ).read_bytes() == skill_body
    assert gateway.read_ids == ["art-skill-manifest", "art-skill-body"]


@pytest.mark.asyncio
async def test_generic_host_rejects_request_skill_authority_drift(tmp_path) -> None:
    service = DeploymentGenericHostServices(
        session_factory=None,
        artifact_gateway=_ArtifactGateway({}),
        credential_materializer=SimpleNamespace(),
        runtime_root=tmp_path / "runtime",
        client=SimpleNamespace(),
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-profile-1",
        correlationId="workflow-1",
        idempotencyKey="step-1",
        resolvedSkillsetRef="art-other-manifest",
    )
    plan = SimpleNamespace(
        planRef="omnigent-execution-plan:sha256:" + "a" * 64,
        payload=SimpleNamespace(harnessId="opencode-native"),
    )
    authority = {
        "executionPlanRef": plan.planRef,
        "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "b" * 64,
    }
    await service.prepare_realization(
        request=request, plan=plan, authority=authority
    )

    with pytest.raises(HarnessPlatformError, match="differs from the admitted plan"):
        await service.materialize(
            {
                "resolvedSkillSetRef": "artifact:art-skill-manifest",
                "resolvedSkillSetDigest": "sha256:" + "d" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "c" * 64,
            },
            authority=authority,
        )


@pytest.mark.asyncio
async def test_deployment_launcher_persists_fenced_host_authority_before_launch(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from moonmind.omnigent.realizers import deployment_adapters

    image_ref = "ghcr.io/example/omnigent-opencode@sha256:" + "1" * 64
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", image_ref)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("OMNIGENT_API_TOKEN", raising=False)
    host_class = get_host_class("omnigent-opencode@1")
    launch_policy = get_launch_policy("opencode-on-demand@1")
    session_factory = _MemorySessionFactory()
    docker_calls: list[tuple[str, ...]] = []

    async def run_docker(argv, *, env=None):
        del env
        docker_calls.append(tuple(argv))
        if argv[0] == "inspect":
            return 1, b"", b"No such object"
        return 0, b"container-id", b""

    async def attest_egress(**_kwargs):
        return SimpleNamespace(network_ref="omnigent-egress")

    credential_file = tmp_path / "credential.json"
    credential_file.write_text(
        '{"apiKey":"integration-only-container-secret"}', encoding="utf-8"
    )
    workspace = tmp_path / "workspace"
    skills = tmp_path / "skills"
    workspace.mkdir()
    skills.mkdir()
    credential_materializer = SimpleNamespace(
        mount_source=lambda _handle: (
            credential_file,
            Path("/home/app/.local/share/opencode/auth.json"),
        )
    )
    monkeypatch.setattr(deployment_adapters, "_run_docker", run_docker)
    monkeypatch.setattr(
        deployment_adapters, "attest_docker_egress", attest_egress
    )
    service = DeploymentGenericHostServices(
        session_factory=session_factory,
        artifact_gateway=_ArtifactGateway({}),
        credential_materializer=credential_materializer,
        runtime_root=tmp_path / "runtime",
        client=SimpleNamespace(),
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-profile-1",
        correlationId="workflow-1",
        idempotencyKey="step-1",
    )
    plan = SimpleNamespace(
        planRef="omnigent-execution-plan:sha256:" + "a" * 64,
        payload=SimpleNamespace(
            harnessId="opencode-native",
            harnessImplementationRef=(
                "omnigent-harness-implementation:sha256:" + "2" * 64
            ),
            credentialBindings={
                "primary-model": SimpleNamespace(
                    providerProfileRef="provider-profile-1"
                )
            },
            supportIdentity=SimpleNamespace(architecture="linux/amd64"),
        ),
    )
    authority = {
        "executionPlanRef": plan.planRef,
        "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "b" * 64,
    }
    await service.prepare_realization(
        request=request, plan=plan, authority=authority
    )

    result = await service.launch(
        host_class=host_class,
        launch_policy=launch_policy,
        workspace_handle={"path": str(workspace)},
        skill_handle={"path": str(skills)},
        credential_handles=[
            {
                "credentialGeneration": 3,
                "materializerRef": "opencode-auth-json@1",
            }
        ],
        authority=authority,
    )

    binding = await session_factory.get(
        OmnigentHostBindingRecordV2, result["hostBindingRef"]
    )
    lease = await session_factory.get(
        OmnigentHostLeaseRecordV2, result["hostLeaseRef"]
    )
    assert binding.execution_plan_ref == plan.planRef
    assert lease.binding_id == binding.binding_id
    assert lease.status == "registering"
    launch = next(call for call in docker_calls if call[0] == "run")
    serialized_launch = json.dumps(launch)
    assert f"moonmind.execution_plan_ref={plan.planRef}" in serialized_launch
    assert (
        f"moonmind.runtime_binding_ref={authority['runtimeBindingRef']}"
        in serialized_launch
    )
    assert "provider-profile-1" not in serialized_launch
    assert "integration-only-container-secret" not in serialized_launch
    assert "/var/run/docker.sock" not in serialized_launch
