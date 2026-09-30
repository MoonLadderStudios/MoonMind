"""Exact-host credential mount attestation for profile-owned OAuth homes."""

from __future__ import annotations

import json

import pytest

from moonmind.omnigent.host_services.attestation import credential_mount_access


class TemplateDockerBackend:
    """Answer Docker template mount queries from a fixed mount table.

    Production command output passes through ``run_runtime_command``, which
    redacts credential-home paths such as ``/home/app/.claude``; the echoed
    ``{{json .Mounts}}`` table therefore reports ``[REDACTED_AUTH_PATH]`` and
    can never be compared. Only Docker's own template evaluator sees the raw
    destination, so this fake returns the bounded token it would print.
    """

    def __init__(self, mounts: list[dict[str, object]]) -> None:
        self.mounts = mounts
        self.calls: list[list[str]] = []

    async def run(self, argv, **_kwargs):
        command = list(argv)
        self.calls.append(command)
        assert command[:4] == ["docker", "container", "inspect", "--format"]
        template = command[4]
        assert "{{json" not in template
        for mount in self.mounts:
            if (
                f"eq .Name {json.dumps(mount['Name'])}" in template
                and f"eq .Destination {json.dumps(mount['Destination'])}"
                in template
            ):
                return 0, "rw" if mount["RW"] else "ro", ""
        return 0, "", ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("volume", "target"),
    (
        ("claude_auth_volume", "/home/app/.claude"),
        ("codex_auth_volume", "/home/app/.codex"),
    ),
)
async def test_oauth_home_mount_is_attested_without_echoing_its_path(
    volume: str, target: str
) -> None:
    backend = TemplateDockerBackend(
        [{"Name": volume, "Destination": target, "RW": True}]
    )

    access = await credential_mount_access(
        backend,
        "mm-host-1",
        {
            "kind": "volume",
            "sourceRef": volume,
            "targetPath": target,
            "accessMode": "read-write",
        },
    )

    assert access == "read-write"
    assert backend.calls[0][-1] == "mm-host-1"


@pytest.mark.asyncio
async def test_missing_and_read_only_mounts_are_reported_exactly() -> None:
    backend = TemplateDockerBackend(
        [
            {
                "Name": "mm-omnigent-credential-abc",
                "Destination": "/run/mm-credentials/opencode",
                "RW": False,
            }
        ]
    )

    read_only = await credential_mount_access(
        backend,
        "mm-host-1",
        {
            "kind": "volume",
            "sourceRef": "mm-omnigent-credential-abc",
            "targetPath": "/run/mm-credentials/opencode",
            "accessMode": "read-only",
        },
    )
    missing = await credential_mount_access(
        backend,
        "mm-host-1",
        {
            "kind": "volume",
            "sourceRef": "claude_auth_volume",
            "targetPath": "/home/app/.claude",
            "accessMode": "read-write",
        },
    )

    assert read_only == "read-only"
    assert missing is None
