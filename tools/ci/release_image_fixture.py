"""Restore an OCI test candidate into a registry owned by one CI job."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPOSITORY = "localhost:5001/moonmind-test-qualification"
OWNER_LABEL = "moonmind.test.release-image-owner"
NAME_PREFIX = "moonmind-test-release-registry-"


class FixtureError(RuntimeError):
    """The isolated image fixture could not be established or cleaned up."""


def _run(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(arguments, check=True, capture_output=True)


def describe(archive: Path) -> dict[str, str]:
    if not archive.is_file() or ":" in str(archive):
        raise FixtureError("Expected an OCI archive at an unambiguous local path")
    raw = _run(["skopeo", "inspect", "--raw", f"oci-archive:{archive}"]).stdout
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return {"image": REPOSITORY + "@" + digest, "digest": digest}


def restore(archive: Path, image: str, state: Path, owner: str) -> None:
    if re.fullmatch(re.escape(REPOSITORY) + r"@sha256:[0-9a-f]{64}", image) is None:
        raise FixtureError("Expected an immutable image in the loopback test registry")
    _check_owner(owner)
    if state.exists():
        raise FixtureError("Registry fixture state already exists; clean it up first")
    if describe(archive)["image"] != image:
        raise FixtureError("OCI archive digest differs from the requested candidate")

    # Persist the unique name before Docker can create anything. Even a partially
    # failed startup can then be reconciled without touching another job's data.
    name = NAME_PREFIX + uuid.uuid4().hex
    with state.open("x") as output:
        json.dump({"registry": name, "owner": owner, "image": image}, output)
    try:
        _run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                name,
                "--label",
                OWNER_LABEL + "=" + owner,
                "-p",
                "127.0.0.1:5001:5000",
                "registry:2",
            ]
        )
        _wait_registry()
        _run(
            [
                "skopeo",
                "copy",
                "--all",
                "--preserve-digests",
                "--dest-no-creds",
                "--dest-tls-verify=false",
                f"oci-archive:{archive}",
                "docker://" + REPOSITORY + ":assembled",
            ]
        )
        raw = _run(
            [
                "skopeo",
                "inspect",
                "--raw",
                "--no-creds",
                "--tls-verify=false",
                "docker://" + image,
            ]
        ).stdout
        if "sha256:" + hashlib.sha256(raw).hexdigest() != image.rsplit("@", 1)[1]:
            raise FixtureError(
                "Restored registry manifest digest differs from candidate"
            )
    except BaseException as error:
        try:
            cleanup(state, owner)
        except Exception as cleanup_error:
            error.add_note(f"Registry fixture cleanup also failed: {cleanup_error}")
        raise


def _check_owner(owner: str) -> None:
    if not owner.strip() or len(owner) > 256 or any(c in owner for c in "\n\r\0"):
        raise FixtureError("A nonempty CI job owner is required")


def _wait_registry() -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:5001/v2/", timeout=1
            ) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            # Connection errors are expected until the local registry starts.
            pass
        time.sleep(0.25)
    raise FixtureError("The job-local test registry did not become ready")


def _confirmed_missing(error: subprocess.CalledProcessError) -> bool:
    return (
        re.search(rb"(?:No such object|No such container):", error.stderr or b"")
        is not None
    )


def cleanup(state: Path, owner: str) -> None:
    _check_owner(owner)
    if not state.exists():
        return
    saved = json.loads(state.read_text())
    if not isinstance(saved, dict):
        raise FixtureError("Registry fixture state must identify its job owner")
    name = saved.get("registry", "")
    if (
        saved.get("owner") != owner
        or not isinstance(name, str)
        or re.fullmatch(re.escape(NAME_PREFIX) + r"[0-9a-f]{32}", name) is None
    ):
        raise FixtureError("Registry fixture state does not match this job owner")
    try:
        labels = json.loads(
            _run(
                ["docker", "inspect", "--format", "{{json .Config.Labels}}", name]
            ).stdout
        )
        if not isinstance(labels, dict) or labels.get(OWNER_LABEL) != owner:
            raise FixtureError("Registry container does not match this job owner")
        _run(["docker", "rm", "-f", name])
    except subprocess.CalledProcessError as error:
        if not _confirmed_missing(error):
            raise
    state.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    description = commands.add_parser("describe")
    description.add_argument("--archive", type=Path, required=True)
    restoration = commands.add_parser("restore")
    restoration.add_argument("--archive", type=Path, required=True)
    restoration.add_argument("--image", required=True)
    for command in (restoration, commands.add_parser("cleanup")):
        command.add_argument("--state", type=Path, required=True)
        command.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "describe":
            print(json.dumps(describe(args.archive)))
        elif args.command == "restore":
            restore(args.archive, args.image, args.state, args.owner)
        else:
            cleanup(args.state, args.owner)
    except (FixtureError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.decode(errors="replace"), file=sys.stderr)
        for note in getattr(error, "__notes__", []):
            print(note, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
