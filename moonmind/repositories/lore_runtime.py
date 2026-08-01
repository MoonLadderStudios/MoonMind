"""Production construction for the pinned Lore repository adapter.

The executable is supplied by the resolved tool bundle.  This boundary verifies
the configured pin before every invocation and accepts machine-readable JSON
only; it never places credentials on the command line.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from .lore_adapter import (
    LORE_WORKSPACE_INVALID,
    LoreImmutableObjectCache,
    LoreRepositoryProviderAdapter,
    LoreWorkspaceError,
)


class PinnedLoreCliClient:
    def __init__(self, *, executable: Path, executable_sha256: str) -> None:
        self._executable = executable.resolve(strict=True)
        self._expected_digest = executable_sha256.removeprefix("sha256:").lower()
        if len(self._expected_digest) != 64:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "invalid Lore executable pin"
            )

    def _invoke(self, arguments: Sequence[str]) -> Mapping[str, object]:
        actual = hashlib.sha256(self._executable.read_bytes()).hexdigest()
        if actual != self._expected_digest:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "pinned Lore executable digest does not match"
            )
        completed = subprocess.run(
            [str(self._executable), *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
            env={
                key: value
                for key, value in os.environ.items()
                if key not in {"LORE_TOKEN", "LORE_PASSWORD"}
            },
        )
        if completed.returncode != 0:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID,
                f"Lore client operation failed with exit code {completed.returncode}",
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID,
                "Lore client did not return machine-readable JSON",
            ) from exc
        if not isinstance(result, dict):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "invalid Lore client result"
            )
        return result

    def materialize(self, *, repository: str, revision: str, destination: Path):
        return self._invoke(
            (
                "workspace",
                "materialize",
                "--repository",
                repository,
                "--revision",
                revision,
                "--destination",
                str(destination),
                "--complete-tree",
            )
        )

    def scan_external_changes(self, *, workspace: Path):
        return self._invoke(
            ("workspace", "scan-external", "--workspace", str(workspace))
        )

    def status(self, *, workspace: Path):
        return self._invoke(("workspace", "status", "--workspace", str(workspace)))

    def stage_paths(self, *, workspace: Path, paths: Sequence[str]) -> None:
        result = self._invoke(
            (
                "workspace",
                "stage",
                "--workspace",
                str(workspace),
                "--paths-json",
                json.dumps(list(paths)),
            )
        )
        if result.get("success") is not True:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "Lore staging did not succeed"
            )


def build_lore_repository_adapter_from_environment() -> (
    LoreRepositoryProviderAdapter | None
):
    """Build the single production adapter, or advertise no Lore support.

    Partial configuration fails at worker startup instead of allowing a Lore run
    to reach launch with an unpinned client.
    """

    executable = os.getenv("MOONMIND_LORE_EXECUTABLE", "").strip()
    digest = os.getenv("MOONMIND_LORE_EXECUTABLE_SHA256", "").strip()
    if not executable and not digest:
        return None
    if not executable or not digest:
        raise LoreWorkspaceError(
            LORE_WORKSPACE_INVALID,
            "MOONMIND_LORE_EXECUTABLE and MOONMIND_LORE_EXECUTABLE_SHA256 are both required",
        )
    cache_root = Path(
        os.getenv("MOONMIND_LORE_IMMUTABLE_CACHE_ROOT", "/var/cache/moonmind/lore")
    )
    limit = int(
        os.getenv("MOONMIND_LORE_CHECKPOINT_LIMIT_BYTES", str(64 * 1024 * 1024))
    )
    return LoreRepositoryProviderAdapter(
        PinnedLoreCliClient(executable=Path(executable), executable_sha256=digest),
        checkpoint_limit_bytes=limit,
        immutable_cache=LoreImmutableObjectCache(cache_root),
    )
