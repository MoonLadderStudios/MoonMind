"""Plan-owned mounted-tool delivery for generic Omnigent hosts."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend

_SAFE_VOLUME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3] / "services/omnigent/tools/manifest.lock.json"
)


def load_mounted_tool_manifest(
    manifest_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load the deployment-owned tool names and pinned metadata."""

    path = Path(manifest_path or _DEFAULT_MANIFEST_PATH).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["tools"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HarnessPlatformError(
            "deployment mounted-tool manifest is unavailable",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        ) from exc
    if not isinstance(rows, list):
        raise HarnessPlatformError(
            "deployment mounted-tool manifest is malformed",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    return {
        str(row.get("name") or "").strip().lower(): dict(row)
        for row in rows
        if isinstance(row, dict) and str(row.get("name") or "").strip()
    }


def deployment_mounted_tool_names(
    manifest_path: str | Path | None = None,
) -> tuple[str, ...]:
    """Return the executable capability names available to new plans."""

    return tuple(sorted(load_mounted_tool_manifest(manifest_path)))


class OmnigentMountedToolService:
    """Resolve profile tool names against the deployment's pinned tool bundle.

    Workflow callers never author source paths, volume identities, or mount
    targets. The immutable plan carries names plus a delivery digest; this
    service maps those names to the one deployment-owned bundle manifest.
    """

    def __init__(
        self,
        *,
        backend: DockerCommandBackend,
        manifest_path: str | Path | None = None,
        volume_ref: str | None = None,
    ) -> None:
        self._backend = backend
        self._manifest_path = Path(manifest_path or _DEFAULT_MANIFEST_PATH).resolve()
        self._volume_ref = str(
            volume_ref
            or os.getenv("MOONMIND_OMNIGENT_TOOLS_VOLUME_REF")
            or f"moonmind-omnigent-tools-gh-{os.getenv('OMNIGENT_GH_VERSION', '2.76.2')}"
        ).strip()

    def _manifest(self) -> dict[str, dict[str, Any]]:
        return load_mounted_tool_manifest(self._manifest_path)

    async def materialize(self, resolved_tools: dict[str, Any]) -> list[dict[str, Any]]:
        delivery_ref = str(resolved_tools.get("toolDeliveryRef") or "").strip()
        requested = resolved_tools.get("tools", [])
        if not delivery_ref.startswith("tool-delivery:sha256:") or not isinstance(
            requested, list
        ):
            raise HarnessPlatformError(
                "resolved tool authority is malformed",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        names = tuple(
            sorted({str(item).strip() for item in requested if str(item).strip()})
        )
        if not names:
            return []
        manifest = self._manifest()
        unknown = sorted(set(names) - set(manifest))
        if unknown:
            raise HarnessPlatformError(
                f"resolved tools are absent from the deployment bundle: {unknown}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if not _SAFE_VOLUME.fullmatch(self._volume_ref):
            raise HarnessPlatformError(
                "deployment mounted-tool volume identity is unsafe",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        await self._backend.run(
            ["docker", "volume", "inspect", self._volume_ref],
            failure_code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
        return [
            {
                "kind": "volume",
                "sourceRef": self._volume_ref,
                "targetPath": "/opt/moonmind-tools",
                "accessMode": "read-only",
                "cleanupRef": None,
                "toolDeliveryRef": delivery_ref,
                "tools": [
                    {
                        "name": name,
                        "version": str(manifest[name].get("version") or ""),
                        "path": str(manifest[name].get("path") or ""),
                        "executableDigests": sorted(
                            {
                                str(platform.get("executableSha256") or "")
                                for platform in dict(
                                    manifest[name].get("platforms") or {}
                                ).values()
                                if isinstance(platform, dict)
                                and platform.get("executableSha256")
                            }
                        ),
                    }
                    for name in names
                ],
            }
        ]


__all__ = [
    "OmnigentMountedToolService",
    "deployment_mounted_tool_names",
    "load_mounted_tool_manifest",
]
