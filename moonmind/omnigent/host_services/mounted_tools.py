"""Image-owned tool delivery for generic Omnigent hosts.

MoonLadderStudios/MoonMind#4558: normal startup no longer depends on a
separately initialized, version-named tools volume. The selected shared host
image owns ``gh`` and ``moonmind`` at ``/opt/moonmind-tools`` for the host's
lifetime; this service resolves plan tool names against the deployment's
pinned tool manifest and binds them to that image-owned path.

Retired volume/initializer settings (``MOONMIND_OMNIGENT_TOOLS_VOLUME_REF``,
``OMNIGENT_TOOL_BUNDLE_VOLUME``, ``OMNIGENT_GH_VERSION``,
``OMNIGENT_TOOL_BUNDLE_VERSION``) are ignored on the new path: a stale
``.env`` or a leftover old volume can neither change tool selection nor block
launch. Persisted pre-cutover attachments with ``kind == "volume"`` remain
honestly readable through :func:`classify_tool_attachment` (legacy drain),
never silently rewritten into image delivery.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

#: Runtime root owned by the selected shared host image.
IMAGE_TOOL_TARGET_PATH = "/opt/moonmind-tools"

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


def classify_tool_attachment(attachment: dict[str, Any]) -> str:
    """Classify a launch-spec tool attachment without rewriting it.

    Returns ``"image"`` for image-owned delivery, ``"legacy-volume-drain"``
    for persisted pre-cutover volume bindings (readable, drained through the
    existing compatibility path), and ``"unsupported"`` otherwise.
    """

    if not isinstance(attachment, dict):
        return "unsupported"
    kind = str(attachment.get("kind") or "").strip().lower()
    target = str(attachment.get("targetPath") or "").strip()
    if kind == "image":
        return "image"
    if kind == "volume" and target == IMAGE_TOOL_TARGET_PATH:
        return "legacy-volume-drain"
    return "unsupported"


class OmnigentMountedToolService:
    """Resolve profile tool names against the deployment's pinned tool manifest.

    Workflow callers never author source paths, volume identities, or mount
    targets. The immutable plan carries names plus a delivery digest; this
    service maps those names to the image-owned tool path of the selected
    host image. No volume is created, inspected, initialized, or required.
    """

    def __init__(
        self,
        *,
        backend: Any | None = None,
        manifest_path: str | Path | None = None,
        image_ref: str | None = None,
        **_retired: Any,
    ) -> None:
        # ``backend`` is retained for call-site compatibility only: image
        # delivery performs no Docker volume inspection. ``volume_ref`` and
        # friends arrive here via ``_retired`` and are ignored so stale
        # configuration cannot change tool selection.
        self._backend = backend
        self._manifest_path = Path(manifest_path or _DEFAULT_MANIFEST_PATH).resolve()
        self._image_ref = str(image_ref or "").strip()

    def _manifest(self) -> dict[str, dict[str, Any]]:
        return load_mounted_tool_manifest(self._manifest_path)

    async def materialize(
        self,
        resolved_tools: dict[str, Any],
        *,
        image_ref: str | None = None,
    ) -> list[dict[str, Any]]:
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
        for name in names:
            probe = manifest[name].get("versionProbe")
            if (
                not isinstance(probe, list)
                or not probe
                or any(not isinstance(arg, str) or not arg for arg in probe)
            ):
                raise HarnessPlatformError(
                    f"deployment mounted-tool probe is malformed for {name}",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
        selected_image = str(image_ref or self._image_ref or "").strip()
        return [
            {
                "kind": "image",
                "sourceRef": f"image:{selected_image}" if selected_image else "image-owned",
                "targetPath": IMAGE_TOOL_TARGET_PATH,
                "accessMode": "read-only",
                "cleanupRef": None,
                "toolDeliveryRef": delivery_ref,
                "tools": [
                    {
                        "name": name,
                        "version": str(manifest[name].get("version") or ""),
                        "path": str(manifest[name].get("path") or ""),
                        "versionProbe": list(manifest[name]["versionProbe"]),
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
    "IMAGE_TOOL_TARGET_PATH",
    "OmnigentMountedToolService",
    "classify_tool_attachment",
    "deployment_mounted_tool_names",
    "load_mounted_tool_manifest",
]
