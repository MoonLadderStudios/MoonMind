"""Docker container and volume inventory for legacy profile-bound hosts.

Source issue: MoonLadderStudios/MoonMind#3711 (required work 3).

Orphan discovery and reclamation for the retained Codex host lifecycle. This is
the container/volume infrastructure detail that used to live inside
``oauth_host_runtime.py``; it now sits behind
:class:`moonmind.omnigent.host_ports.OmnigentHostContainerInventoryPort` so the
janitor depends on one narrow capability instead of the whole host runtime.

Every operation is ownership-scoped by the deployment's Omnigent host label. An
unlabeled or foreign container is refused with the shared host failure
vocabulary, never silently inspected or removed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from moonmind.omnigent.host_failures import OmnigentOAuthHostError

# Label authority for a MoonMind-owned legacy Omnigent host container.
HOST_OWNERSHIP_LABEL = "moonmind.kind"
HOST_OWNERSHIP_VALUE = "omnigent-oauth-host"
HOST_LEASE_LABEL = "moonmind.host_lease_id"

# Run-owned volumes created beside a host container, reclaimed with it.
HOST_VOLUME_SUFFIXES = ("state", "artifacts", "cache")

RuntimeCommand = Callable[..., Awaitable[tuple[int, str, str]]]


class LegacyOmnigentHostContainerService:
    """Ownership-scoped Docker inventory and reclamation for legacy hosts."""

    def __init__(self, *, run_command: RuntimeCommand) -> None:
        self._run = run_command

    async def container_exists(self, container_name: str) -> bool:
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}",
            container_name,
            check=False,
        )
        return result[0] == 0 and result[1].strip() == "true"

    async def container_present(self, container_name: str) -> bool:
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            "{{.Id}}",
            container_name,
            check=False,
        )
        return result[0] == 0 and bool(result[1].strip())

    async def volume_present(self, volume_name: str) -> bool:
        result = await self._run(
            "docker",
            "volume",
            "inspect",
            "--format",
            "{{.Name}}",
            volume_name,
            check=False,
        )
        return result[0] == 0 and bool(result[1].strip())

    async def list_managed_containers(self) -> list[str]:
        result = await self._run(
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={HOST_OWNERSHIP_LABEL}={HOST_OWNERSHIP_VALUE}",
            "--format",
            "{{.Names}}",
            check=False,
        )
        if result[0] != 0:
            return []
        return [line.strip() for line in result[1].splitlines() if line.strip()]

    async def managed_container_host_lease_ref(
        self, container_name: str
    ) -> str | None:
        """Return the durable lease identity carried by a managed container."""

        result = await self._run(
            "docker",
            "inspect",
            "--format",
            (
                f'{{{{index .Config.Labels "{HOST_OWNERSHIP_LABEL}"}}}}|'
                f'{{{{index .Config.Labels "{HOST_LEASE_LABEL}"}}}}'
            ),
            container_name,
            check=False,
        )
        if result[0] != 0:
            return None
        kind, separator, lease_ref = result[1].strip().partition("|")
        if not separator or kind != HOST_OWNERSHIP_VALUE:
            raise OmnigentOAuthHostError(
                "refusing to inspect a container outside Omnigent ownership",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )
        return lease_ref.strip() or None

    async def remove_container(self, container_name: str) -> None:
        # Janitor discovery is label-scoped; never accept an arbitrary name.
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            f'{{{{index .Config.Labels "{HOST_OWNERSHIP_LABEL}"}}}}',
            container_name,
            check=False,
        )
        if result[0] != 0:
            return
        if result[1].strip() != HOST_OWNERSHIP_VALUE:
            raise OmnigentOAuthHostError(
                "refusing to remove a container outside Omnigent ownership",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )
        await self._run("docker", "rm", "-f", container_name, check=False)
        volume_names = tuple(
            f"{container_name}-{suffix}" for suffix in HOST_VOLUME_SUFFIXES
        )
        for volume_name in volume_names:
            await self._run(
                "docker", "volume", "rm", "-f", volume_name, check=False
            )
        remaining_container = await self.container_present(container_name)
        remaining_volumes = [
            name for name in volume_names if await self.volume_present(name)
        ]
        if remaining_container or remaining_volumes:
            raise OmnigentOAuthHostError(
                "orphaned Omnigent host cleanup could not be reconciled",
                code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
            )

    async def assert_container_owned(
        self, *, container_name: str, lease_id: str
    ) -> None:
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            f'{{{{index .Config.Labels "{HOST_LEASE_LABEL}"}}}}',
            container_name,
            check=False,
        )
        if result[0] != 0 or result[1].strip() != lease_id:
            raise OmnigentOAuthHostError(
                "container does not belong to the current host lease",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )


__all__ = [
    "HOST_LEASE_LABEL",
    "HOST_OWNERSHIP_LABEL",
    "HOST_OWNERSHIP_VALUE",
    "HOST_VOLUME_SUFFIXES",
    "LegacyOmnigentHostContainerService",
]
