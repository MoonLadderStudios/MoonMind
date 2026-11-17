"""Docker container and volume reclamation for OAuth host-ledger leases.

Orphan discovery and reclamation for hosts recorded in the OAuth host ledger:
credential validators and hosts left by the removed profile-bound lifecycle.
The janitor, credential maintenance and the retained session supervisor depend
on this one narrow capability
(:class:`moonmind.omnigent.host_ports.OmnigentHostContainerInventoryPort` plus
``stop_host``); nothing launches or writes to these hosts any more, so
reclamation needs no writer fence first.

Every operation is ownership-scoped by the deployment's Omnigent host label. An
unlabeled or foreign container is refused with the shared host failure
vocabulary, never silently inspected or removed.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.oauth_hosts import deterministic_host_container_name

# Label authority for a MoonMind-owned legacy Omnigent host container.
HOST_OWNERSHIP_LABEL = "moonmind.kind"
HOST_OWNERSHIP_VALUE = "omnigent-oauth-host"
HOST_LEASE_LABEL = "moonmind.host_lease_id"

# Run-owned volumes created beside a host container, reclaimed with it.
HOST_VOLUME_SUFFIXES = ("state", "artifacts", "cache")

RuntimeCommand = Callable[..., Awaitable[tuple[int, str, str]]]


def initializer_container_name(container_name: str) -> str:
    return f"{container_name}-init"


class LegacyOmnigentHostContainerService:
    """Ownership-scoped Docker inventory and reclamation for legacy hosts."""

    def __init__(
        self,
        *,
        run_command: RuntimeCommand,
        runtime_scripts_root: Path | None = None,
    ) -> None:
        self._run = run_command
        # Profile-bound launches wrote per-lease runtime scripts and capability
        # files under the workspace root; reclaiming a lease removes them.
        self._runtime_scripts_root = (
            runtime_scripts_root
            or Path(os.getenv("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs"))
            / ".omnigent-runtime-scripts"
        )

    async def stop_host(self, *, binding: Any, host_lease: Any) -> dict[str, Any]:
        """Remove one lease's container, owned volumes and runtime files."""

        del binding
        container_name = host_lease.container_name or deterministic_host_container_name(
            host_lease.lease_id
        )
        if await self.container_present(container_name):
            await self.assert_container_owned(
                container_name=container_name, lease_id=host_lease.lease_id
            )
        await self.remove_initializer(
            container_name=container_name, lease_id=host_lease.lease_id
        )
        await self._run("docker", "stop", "--time", "20", container_name, check=False)
        await self._run("docker", "rm", "-f", container_name, check=False)
        volume_names = tuple(
            f"{container_name}-{suffix}" for suffix in HOST_VOLUME_SUFFIXES
        )
        for volume_name in volume_names:
            await self._run("docker", "volume", "rm", "-f", volume_name, check=False)
        container_present = await self.container_present(container_name)
        remaining_volumes = [
            name for name in volume_names if await self.volume_present(name)
        ]
        runtime_files_removed = False
        if not container_present and not remaining_volumes:
            try:
                runtime_files_removed = self._remove_runtime_scripts(
                    host_lease.lease_id
                )
            except OSError:
                runtime_files_removed = False
        cleanup_ok = runtime_files_removed
        result: dict[str, Any] = {
            "cleanupResult": "succeeded" if cleanup_ok else "failed",
            "reconciliationResult": "succeeded" if cleanup_ok else "required",
            "cleanupValidatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "attachmentIdentity": container_name,
            "resourceCleanup": {
                "containerPresent": container_present,
                "remainingOwnedVolumes": remaining_volumes,
                "runtimeCapabilityFilesRemoved": runtime_files_removed,
                "mode": "on_demand_remove",
            },
        }
        if not cleanup_ok:
            raise OmnigentOAuthHostError(
                "Omnigent host cleanup could not be reconciled",
                code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
                cleanup_evidence=result,
            )
        return result

    def _remove_runtime_scripts(self, lease_id: str) -> bool:
        root = self._runtime_scripts_root.resolve()
        digest = hashlib.sha256(lease_id.encode("utf-8")).hexdigest()[:24]
        target = (root / digest).resolve()
        if not target.is_relative_to(root):
            return False
        if not target.exists():
            return True
        if target.is_symlink() or any(path.is_symlink() for path in target.rglob("*")):
            return False
        for path in sorted(target.rglob("*"), reverse=True):
            path.chmod(0o700 if path.is_dir() else 0o600)
        target.chmod(0o700)
        shutil.rmtree(target)
        return not target.exists()

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

    async def remove_initializer(self, *, container_name: str, lease_id: str) -> None:
        """Reconcile the lease's credential consumer before releasing its mounts."""

        name = initializer_container_name(container_name)

        async def present() -> bool:
            code, output, _error = await self._run(
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^/{re.escape(name)}$",
                "--format",
                "{{.Names}}",
                check=False,
            )
            if code != 0:
                raise OmnigentOAuthHostError(
                    "OAuth host initializer presence could not be confirmed",
                    code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
                )
            return bool(output.strip())

        if not await present():
            return
        owner = await self.managed_container_host_lease_ref(name)
        if owner != lease_id:
            if owner is None and not await present():
                return  # A successful --rm exit raced the ownership probe.
            raise OmnigentOAuthHostError(
                "initializer does not belong to the current host lease",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )
        await self._run("docker", "rm", "-f", name, check=False)
        if await present():
            raise OmnigentOAuthHostError(
                "OAuth host initializer cleanup could not be reconciled",
                code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
            )

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

    async def managed_container_host_lease_ref(self, container_name: str) -> str | None:
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
            await self._run("docker", "volume", "rm", "-f", volume_name, check=False)
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
    "initializer_container_name",
]
