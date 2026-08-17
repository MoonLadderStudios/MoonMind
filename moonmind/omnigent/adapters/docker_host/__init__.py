"""Host + lease adapters (Docker host mode).

The in-memory implementations here are the separately-testable reference for the
host-launch and lease ports. The production Docker launcher (image and egress
attestation, mounted-tool preflight) is extracted from ``oauth_host_runtime`` in
a later phase of MoonLadderStudios/MoonMind#3711; the port and its contract are
established now so that extraction lands behind a stable seam.
"""

from moonmind.omnigent.adapters.docker_host.launcher import (
    InMemoryHostLauncher,
    InMemoryLeaseManager,
)

__all__ = ["InMemoryHostLauncher", "InMemoryLeaseManager"]
