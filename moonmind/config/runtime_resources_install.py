"""Install a deployment-owned resource package with recoverable build inputs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from dotenv import dotenv_values

from moonmind.config.container_backend_settings import (
    LocalImageRecipe,
    resolve_container_backend_settings,
)
from moonmind.config.runtime_resources import load_runtime_resources


def _copy_context(source: Path, target: Path) -> None:
    """Copy a self-contained build context; never follow external links."""
    for entry in source.rglob("*"):
        if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
            raise ValueError(
                "Resource build contexts must contain ordinary files and directories."
            )
    shutil.copytree(source, target, copy_function=shutil.copy2)


def _package_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Installed resource package contains a linked path.")
        metadata = json.dumps(
            [
                path.relative_to(root).as_posix(),
                path.stat().st_mode & 0o777,
                "directory" if path.is_dir() else "file",
            ]
        ).encode()
        digest.update(len(metadata).to_bytes(8, "big"))
        digest.update(metadata)
        if path.is_file():
            contents = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    contents.update(chunk)
            digest.update(contents.digest())
    return digest.hexdigest()


def _atomic_write(path: Path, contents: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".resources-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_runtime_resources(source: Path, project_dir: Path) -> Path:
    """Atomically select installed resources, retaining every previous package.

    The stable mount root is never renamed. API and worker bind mounts see the
    same new file, and a build already using an older package keeps its inputs.
    No image, network, container, or upstream service is mutated by installation.
    """
    source = source.resolve(strict=True)
    manifest = source / "resources.json" if source.is_dir() else source
    resources = load_runtime_resources(
        {"MOONMIND_RUNTIME_RESOURCES_FILE": str(manifest)}
    )
    settings = resolve_container_backend_settings(
        {"MOONMIND_RUNTIME_RESOURCES_FILE": str(manifest)}
    )
    if not project_dir.resolve(strict=True).is_dir():
        raise ValueError("The deployment project directory is unavailable.")
    setting = "MOONMIND_RUNTIME_RESOURCES_DIRECTORY"
    configured = (
        os.environ[setting]
        if setting in os.environ
        else dotenv_values(project_dir / ".env").get(setting)
    )
    destination = project_dir.resolve() / (configured or "deploy/runtime-resources")
    destination = destination.expanduser().absolute()
    # Installed data is deployment authority; do not redirect it through links.
    for path in (destination.parent, destination, destination / "packages"):
        if path.is_symlink():
            raise ValueError("The deployment resource directory must not be linked.")
    packages = destination / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    # Parse again from the admitted file so absent optional envelope fields keep
    # their ordinary defaults; the loader rejected duplicate/unknown fields.
    payload = {
        "schemaVersion": 1,
        "imageSources": [dict(row) for row in resources.image_sources],
        "cacheSources": list(resources.cache_sources),
        "serviceConnections": list(resources.service_connections),
        "environment": dict(resources.environment),
    }
    with tempfile.TemporaryDirectory(prefix=".install-", dir=packages) as temporary:
        staged = Path(temporary) / "package"
        staged.mkdir()
        for index, row in enumerate(payload["imageSources"]):
            recipe = settings.image_source(row["sourceRef"])
            if isinstance(recipe, LocalImageRecipe):
                if destination.is_relative_to(recipe.context_root):
                    raise ValueError(
                        "Resource build context must not contain its installation directory."
                    )
                relative = f"contexts/{index}"
                _copy_context(recipe.context_root, staged / relative)
                row["build"] = {**row["build"], "context": relative}
        staged_manifest = staged / "resources.json"
        staged_manifest.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        resolve_container_backend_settings(
            {"MOONMIND_RUNTIME_RESOURCES_FILE": str(staged_manifest)}
        )
        identity = _package_digest(staged)
        retained = packages / identity
        if retained.exists():
            if retained.is_symlink() or _package_digest(retained) != identity:
                raise ValueError(
                    "An installed resource package failed its integrity check."
                )
        else:
            try:
                staged.rename(retained)
            except FileExistsError:
                if retained.is_symlink() or _package_digest(retained) != identity:
                    raise ValueError(
                        "Concurrent resource installation failed its integrity check."
                    ) from None
        # Compose incorporates this identity in API/worker service configuration.
        # Its normal changed-service apply activates the same immutable package
        # in both processes. A process without that activation identity cannot
        # first-read a staged package before the controller recreates it.
        _atomic_write(
            destination / "activation.env",
            f"MOONMIND_RUNTIME_RESOURCES_DIGEST={identity}\n",
        )
    return retained / "resources.json"
