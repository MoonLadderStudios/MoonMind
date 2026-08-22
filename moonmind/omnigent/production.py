"""All-or-nothing production assembly for the generic Omnigent host plane."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from moonmind.auth.resolvers import (
    DbEncryptedSecretResolver,
    EnvSecretResolver,
    ExecSecretResolver,
    RootSecretResolver,
)
from moonmind.auth.secret_refs import SecretBackend
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.credential_materializers import (
    OmnigentCredentialProvisioningService,
    build_default_credential_materializer_registry,
)
from moonmind.omnigent.control_plane.repositories import OmnigentControlPlaneStore
from moonmind.omnigent.control_plane.turn_commands import CanonicalTurnCommandService
from moonmind.omnigent.execute import run_omnigent_execution
from moonmind.omnigent.harness_platform.catalog_service import (
    DbHarnessCatalogRepository,
    OmnigentHarnessCatalogService,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import (
    DEFAULT_HOST_CLASS_TEMPLATES,
    OmnigentHostClassSelector,
)
from moonmind.omnigent.harness_platform.planning_service import (
    ArtifactPlanningSkillResolver,
    OmnigentExecutionPlanningService,
    OmnigentPlannedHostResolver,
)
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanUsageStore
from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository
from moonmind.omnigent.host_runtime import GenericOmnigentHostRuntime
from moonmind.omnigent.host_services import (
    DockerCommandBackend,
    DockerOmnigentHostAttestor,
    DockerOmnigentHostCleanupService,
    DockerOmnigentHostLauncher,
    OmnigentEgressService,
    OmnigentHostRegistrationService,
    OmnigentMountedToolService,
    OmnigentRuntimeScriptService,
    OmnigentSkillDeliveryService,
    OmnigentWorkspaceMaterializer,
)
from moonmind.omnigent.provider_leases import OmnigentProviderLeaseCoordinator
from moonmind.omnigent.realizers.codex_profile_bound import CodexProfileBoundRealizer
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.realizers.registry import OmnigentExecutionRealizerRegistry
from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore
from moonmind.omnigent.secret_resolution import OmnigentSecretResolutionService
from moonmind.omnigent.session_cleanup import OmnigentSessionCleanupService
from moonmind.omnigent.settings import (
    generic_host_enabled,
    opencode_support_enabled,
    resolved_api_token,
    resolved_host_runner_token,
    resolved_proxy_forward_headers,
    resolved_server_url,
)
from moonmind.provider_profiles.lease_client import ProviderProfileLeaseClient
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.client import TemporalClientAdapter

_IMAGE_REF = re.compile(r"^.+@sha256:([0-9a-f]{64})$")


@dataclass(frozen=True)
class GenericOmnigentExecutionServices:
    planning_service: OmnigentExecutionPlanningService
    realizer_registry: OmnigentExecutionRealizerRegistry
    catalog_service: OmnigentHarnessCatalogService
    runtime_binding_store: DbRuntimeBindingStore
    host_lease_repository: DbOmnigentHostLeaseRepository
    generic_realizer: GenericOmnigentHostRealizer


def _server_build_digest() -> str:
    explicit = str(os.getenv("OMNIGENT_BUILD_DIGEST") or "").strip()
    if explicit:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", explicit):
            return explicit
        raise HarnessPlatformError(
            "OMNIGENT_BUILD_DIGEST must be an exact sha256 identity",
            code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
        )
    image_ref = str(os.getenv("OMNIGENT_IMAGE_REF") or "").strip()
    match = _IMAGE_REF.fullmatch(image_ref)
    if match is None:
        raise HarnessPlatformError(
            "OMNIGENT_IMAGE_REF must identify the exact Omnigent server digest",
            code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
        )
    return f"sha256:{match.group(1)}"


def _production_host_class_selector() -> OmnigentHostClassSelector:
    templates = tuple(
        template
        for template in DEFAULT_HOST_CLASS_TEMPLATES
        if template.host_class_id != "omnigent-opencode" or opencode_support_enabled()
    )
    return OmnigentHostClassSelector(templates=templates)


def build_omnigent_secret_resolver() -> RootSecretResolver:
    # Vault is deliberately not inferred: a deployment that uses vault:// refs
    # must register its configured Vault adapter at the canonical root boundary.
    return RootSecretResolver(
        {
            SecretBackend.ENV: EnvSecretResolver(),
            SecretBackend.DB_ENCRYPTED: DbEncryptedSecretResolver(),
            SecretBackend.EXEC: ExecSecretResolver(),
        }
    )


def build_generic_omnigent_execution_services(
    *,
    session_factory: Any,
    artifact_gateway: Any | None = None,
    run_store: Any | None = None,
) -> GenericOmnigentExecutionServices:
    """Build a complete registry or fail before the worker handles a request."""

    if not generic_host_enabled():
        raise HarnessPlatformError(
            "generic Omnigent host execution is disabled",
            code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
        )
    server_url = resolved_server_url()
    host_server_url = str(os.getenv("MOONMIND_OMNIGENT_HOST_SERVER_URL") or "").strip()
    expected_owner = str(
        os.getenv("MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER") or ""
    ).strip()
    if not server_url or not host_server_url or not expected_owner:
        raise HarnessPlatformError(
            "generic Omnigent host endpoint and owner configuration is incomplete",
            code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
        )
    artifacts = artifact_gateway or TemporalOmnigentArtifactGateway(session_factory)
    bridge_store = run_store or OmnigentBridgeSessionStore(session_factory)
    client = OmnigentHttpClient(
        base_url=server_url,
        api_token=resolved_api_token(),
        upstream_header_allowlist=resolved_proxy_forward_headers(),
    )
    catalogs = DbHarnessCatalogRepository(session_factory)
    selector = _production_host_class_selector()
    planning = OmnigentExecutionPlanningService(
        session_factory=session_factory,
        catalog_repository=catalogs,
        plan_usage_store=DbExecutionPlanUsageStore(session_factory),
        skill_resolver=ArtifactPlanningSkillResolver(artifacts),
        artifact_gateway=artifacts,
        host_class_selector=selector,
    )
    docker = DockerCommandBackend()

    async def daemon_command(argv: list[str]) -> tuple[int, str, str]:
        return await docker.run(argv, check=False)

    workspace_root = os.getenv("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs")
    workspace_volume = os.getenv(
        "MOONMIND_AGENT_WORKSPACES_VOLUME_NAME", "agent_workspaces"
    )
    credentials = OmnigentCredentialProvisioningService(
        session_factory=session_factory,
        secret_resolution_service=OmnigentSecretResolutionService(
            session_factory=session_factory,
            resolver=build_omnigent_secret_resolver(),
        ),
        registry=build_default_credential_materializer_registry(),
        artifact_gateway=artifacts,
    )
    host_runtime = GenericOmnigentHostRuntime(
        launcher=DockerOmnigentHostLauncher(
            backend=docker,
            runtime_scripts=OmnigentRuntimeScriptService(),
            server_url=host_server_url,
            host_api_token=resolved_host_runner_token(),
        ),
        workspace_service=OmnigentWorkspaceMaterializer(
            command_runner=daemon_command,
            workspace_root=workspace_root,
            workspace_volume=workspace_volume,
        ),
        skill_service=OmnigentSkillDeliveryService(
            workspace_root=workspace_root,
            workspace_volume=workspace_volume,
            command_runner=daemon_command,
            artifact_gateway=artifacts,
        ),
        tool_service=OmnigentMountedToolService(backend=docker),
        egress_service=OmnigentEgressService(backend=docker, artifacts=artifacts),
        registration_waiter=OmnigentHostRegistrationService(
            client=client, expected_owner=expected_owner
        ),
        host_attestor=DockerOmnigentHostAttestor(
            backend=docker, client=client, artifacts=artifacts
        ),
        cleanup_service=DockerOmnigentHostCleanupService(docker),
    )

    async def session_driver(request, *, session_authority_sink):
        return await run_omnigent_execution(
            request,
            artifact_gateway=artifacts,
            run_store=bridge_store,
            session_authority_sink=session_authority_sink,
        )

    runtime_bindings = DbRuntimeBindingStore(session_factory)
    host_leases = DbOmnigentHostLeaseRepository(session_factory)
    realizer = GenericOmnigentHostRealizer(
        runtime_binding_store=runtime_bindings,
        provider_lease_coordinator=OmnigentProviderLeaseCoordinator(
            session_factory=session_factory,
            lease_client=ProviderProfileLeaseClient(TemporalClientAdapter()),
        ),
        credential_provisioning_service=credentials,
        host_lease_repository=host_leases,
        host_runtime=host_runtime,
        planned_host_resolver=OmnigentPlannedHostResolver(
            catalog_repository=catalogs,
            host_class_selector=selector,
            artifact_gateway=artifacts,
        ),
        session_driver=session_driver,
        session_cleanup_service=OmnigentSessionCleanupService(client),
        artifact_gateway=artifacts,
        turn_command_service=CanonicalTurnCommandService(
            OmnigentControlPlaneStore(session_factory)
        ),
    )
    registry = OmnigentExecutionRealizerRegistry()
    registry.register(CodexProfileBoundRealizer(session_factory=session_factory))
    registry.register(realizer)
    return GenericOmnigentExecutionServices(
        planning_service=planning,
        realizer_registry=registry,
        catalog_service=OmnigentHarnessCatalogService(
            client=client,
            repository=catalogs,
            endpoint_ref="default",
            omnigent_build_digest=_server_build_digest(),
        ),
        runtime_binding_store=runtime_bindings,
        host_lease_repository=host_leases,
        generic_realizer=realizer,
    )


__all__ = [
    "GenericOmnigentExecutionServices",
    "build_generic_omnigent_execution_services",
    "build_omnigent_secret_resolver",
]
