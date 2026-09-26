"""Concrete reusable services for MoonMind-owned Omnigent hosts."""

from moonmind.omnigent.host_services.attestation import DockerOmnigentHostAttestor
from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.egress import OmnigentEgressService
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
)
from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
from moonmind.omnigent.host_services.legacy_host_containers import (
    LegacyOmnigentHostContainerService,
)
from moonmind.omnigent.host_services.legacy_tools_cleanup import (
    apply_legacy_cleanup,
    classify_legacy_tools_volume,
    plan_legacy_cleanup,
)
from moonmind.omnigent.host_services.mounted_tools import (
    IMAGE_TOOL_TARGET_PATH,
    OmnigentMountedToolService,
    classify_tool_attachment,
)
from moonmind.omnigent.host_services.registration import OmnigentHostRegistrationService
from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from moonmind.omnigent.host_services.skills import OmnigentSkillDeliveryService
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer

__all__ = [
    "DockerCommandBackend",
    "DockerOmnigentHostAttestor",
    "DockerOmnigentHostCleanupService",
    "DockerOmnigentHostLauncher",
    "IMAGE_TOOL_TARGET_PATH",
    "LegacyOmnigentHostContainerService",
    "OmnigentEgressService",
    "OmnigentGithubCredentialService",
    "OmnigentHostRegistrationService",
    "OmnigentMountedToolService",
    "OmnigentRuntimeEnvironmentService",
    "OmnigentRuntimeScriptService",
    "OmnigentSkillDeliveryService",
    "OmnigentWorkspaceMaterializer",
    "apply_legacy_cleanup",
    "classify_legacy_tools_volume",
    "classify_tool_attachment",
    "plan_legacy_cleanup",
]
