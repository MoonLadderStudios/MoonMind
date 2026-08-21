"""Infrastructure composition for versioned Omnigent execution realizers.

This module is the only generic-realizer boundary allowed to depend on API
persistence and concrete runtime services. Application coordination in
``generic_host`` consumes injected capabilities and stays framework-neutral.
"""

from __future__ import annotations

from typing import Any

from api_service.db.base import async_session_maker

from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.control_plane.turn_commands import (
    CanonicalTurnCommandService,
)
from moonmind.omnigent.control_plane.repositories import OmnigentControlPlaneStore
from moonmind.omnigent.execute import run_omnigent_execution
from moonmind.omnigent.harness_platform.stores import DbRuntimeBindingStore
from moonmind.omnigent.host_runtime import GenericOmnigentHostRuntime
from moonmind.omnigent.realizers.deployment_adapters import (
    DeploymentGenericHostServices,
    TrustedCredentialMaterializer,
)
from moonmind.omnigent.realizers.generic_host import GenericRealizerDependencies
from moonmind.omnigent.realizers.runtime_authority import (
    ProviderProfileRuntimeAuthority,
)


def build_generic_realizer_dependencies(
    session_factory: Any | None = None,
) -> GenericRealizerDependencies:
    """Build the production adapter graph without interpreting a harness id."""

    factory = session_factory or async_session_maker
    artifact_gateway = LocalOmnigentArtifactGateway()
    run_store = OmnigentBridgeSessionStore(factory)
    credential_materializer = TrustedCredentialMaterializer(
        session_factory=factory
    )
    host_services = DeploymentGenericHostServices(
        session_factory=factory,
        artifact_gateway=artifact_gateway,
        credential_materializer=credential_materializer,
    )

    async def execute(request: Any) -> Any:
        return await run_omnigent_execution(
            request,
            artifact_gateway=artifact_gateway,
            run_store=run_store,
        )

    return GenericRealizerDependencies(
        runtime_binding_store=DbRuntimeBindingStore(factory),
        runtime_authority=ProviderProfileRuntimeAuthority(
            session_factory=factory,
            credential_materializer=credential_materializer,
        ),
        host_runtime=GenericOmnigentHostRuntime(
            launcher=host_services,
            workspace_service=host_services,
            skill_service=host_services,
            egress_service=host_services,
            registration_waiter=host_services,
            image_attestor=host_services,
            cleanup_service=host_services,
            context_service=host_services,
        ),
        turn_command_service=CanonicalTurnCommandService(
            OmnigentControlPlaneStore(factory)
        ),
        execution_driver=execute,
    )


__all__ = ["build_generic_realizer_dependencies"]
