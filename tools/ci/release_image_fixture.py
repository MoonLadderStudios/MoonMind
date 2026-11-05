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

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ci.source_verification import artifact_attempt

REPOSITORY = "localhost:5001/moonmind-test-qualification"
OWNER_LABEL = "moonmind.test.release-image-owner"
NAME_PREFIX = "moonmind-test-release-registry-"
RECOVERY_REGISTRY = "127.0.0.1:5000"
RECOVERY_IMAGES = {
    "server": "moonmind-test-omnigent-server",
    "host": "moonmind-test-omnigent-host",
}
RECOVERY_SCHEMA = "moonmind.recovery-images/v2"
BLOB_ROOT = ("docker", "registry", "v2", "blobs", "sha256")


class FixtureError(RuntimeError):
    """The isolated image fixture could not be established or cleaned up."""


def _file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()


def _storage_files(root: Path) -> dict[str, tuple[str, int]]:
    """Hash transported registry files; content-addressed blobs must match."""
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise FixtureError("Recovery registry storage has a non-regular entry")
        if path.is_dir():
            continue
        relative = path.relative_to(root).parts
        digest = _file_digest(path)
        if relative[:5] == BLOB_ROOT and (
            len(relative) != 8
            or relative[7] != "data"
            or relative[5] != relative[6][:2]
            or digest != "sha256:" + relative[6]
        ):
            raise FixtureError("Recovery registry blob differs from its digest")
        files["/".join(relative)] = (digest, path.stat().st_size)
    return files


def _storage_digest(files: dict[str, tuple[str, int]]) -> str:
    listing = "".join(f"{path}\0{digest}\n" for path, (digest, _) in files.items())
    return "sha256:" + hashlib.sha256(listing.encode()).hexdigest()


def _blob_path(digest: str) -> str:
    hex_digest = digest.removeprefix("sha256:")
    return "/".join((*BLOB_ROOT, hex_digest[:2], hex_digest, "data"))


def _image_blobs(
    root: Path, files: dict[str, tuple[str, int]], digest: str
) -> dict[str, int]:
    """Return the verified manifest closure one registry image needs."""
    path = _blob_path(digest)
    if path not in files:
        raise FixtureError("Recovery registry storage lacks a referenced manifest")
    manifest = json.loads((root / path).read_bytes())
    blobs = {digest: files[path][1]}
    for child in manifest.get("manifests", []):
        blobs.update(_image_blobs(root, files, child["digest"]))
    for descriptor in (manifest.get("config"), *manifest.get("layers", [])):
        if descriptor:
            blob = _blob_path(descriptor["digest"])
            if blob not in files:
                raise FixtureError("Recovery registry storage lacks a referenced blob")
            blobs[descriptor["digest"]] = files[blob][1]
    return blobs


def _recovery_identity(
    revision: str, upstream: str, run_id: str, attempt: str
) -> dict[str, str]:
    if (
        not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (revision, upstream))
        or not run_id.isdecimal()
        or not attempt.isdecimal()
    ):
        raise FixtureError("Missing immutable recovery source/run identity")
    return {
        "revision": revision,
        "upstream": upstream,
        "runId": run_id,
        "attempt": attempt,
    }


def _inspect_recovery_image(
    role: str, image: str, *, revision: str, upstream: str
) -> dict:
    inspected = json.loads(_run(["docker", "image", "inspect", image]).stdout)[0]
    labels = inspected.get("Config", {}).get("Labels", {}) or {}
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", inspected.get("Id", ""))
        or labels.get("org.opencontainers.image.revision") != upstream
    ):
        raise FixtureError("Recovery runtime lacks its pinned source identity")
    if role == "host" and labels.get("moonmind.source.revision") != revision:
        raise FixtureError("Recovery host overlay belongs to another source")
    return inspected


def save_recovery(
    root: Path, state: Path, *, revision: str, upstream: str, run_id: str, attempt: str
) -> None:
    """Record the job-local registry storage that transports both runtimes.

    Its blobs are already compressed and shared layers are stored once, so the
    consumer serves them directly instead of loading and re-pushing an export.
    """
    identity = _recovery_identity(revision, upstream, run_id, attempt)
    files = _storage_files(root)
    images, blobs = {}, {}
    for role, name in RECOVERY_IMAGES.items():
        repository = f"{RECOVERY_REGISTRY}/{name}"
        inspected = _inspect_recovery_image(
            role, repository + ":candidate", revision=revision, upstream=upstream
        )
        refs = [
            ref
            for ref in inspected.get("RepoDigests", [])
            if re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", ref)
        ]
        if len(refs) != 1:
            raise FixtureError("Recovery runtime is not pinned in its job registry")
        blobs[role] = _image_blobs(root, files, refs[0].rsplit("@", 1)[1])
        images[name] = {
            "ref": refs[0],
            "id": inspected["Id"],
            "bytes": sum(blobs[role].values()),
        }
    required = blobs["server"] | blobs["host"]
    measured = {
        "storageBytes": sum(size for _, size in files.values()),
        "requiredBytes": sum(required.values()),
        "sharedBytes": sum(
            required[blob] for blob in blobs["server"].keys() & blobs["host"]
        ),
    }
    payload = {
        "schema": RECOVERY_SCHEMA,
        **identity,
        "images": images,
        "storageDigest": _storage_digest(files),
        **measured,
    }
    state.write_text(json.dumps(payload, indent=2) + "\n")
    print(
        "::notice title=Recovery runtime bytes::"
        f"server={images[RECOVERY_IMAGES['server']]['bytes']} "
        f"host={images[RECOVERY_IMAGES['host']]['bytes']} "
        f"shared={measured['sharedBytes']} required={measured['requiredBytes']} "
        f"transported={measured['storageBytes']}"
    )


def restore_recovery(
    root: Path,
    state: Path,
    *,
    revision: str,
    upstream: str,
    run_id: str,
    attempt: str,
    artifact: str | None = None,
) -> dict[str, str]:
    """Verify transported storage, then pull the exact recorded runtimes.

    The caller serves ``root`` read-only from this job's loopback registry.
    """
    if artifact:
        attempt = artifact_attempt(
            artifact, prefix="recovery-images", run_id=run_id, current_attempt=attempt
        )
    identity = _recovery_identity(revision, upstream, run_id, attempt)
    payload = json.loads(state.read_text())
    images = payload.get("images") if isinstance(payload, dict) else None
    if (
        not isinstance(images, dict)
        or payload.get("schema") != RECOVERY_SCHEMA
        or any(payload.get(key) != value for key, value in identity.items())
        or set(images) != set(RECOVERY_IMAGES.values())
        or any(
            not isinstance(images[name], dict)
            or re.fullmatch(
                re.escape(f"{RECOVERY_REGISTRY}/{name}") + r"@sha256:[0-9a-f]{64}",
                str(images[name].get("ref", "")),
            )
            is None
            for name in images
        )
        or payload.get("storageDigest") != _storage_digest(_storage_files(root))
    ):
        raise FixtureError("Recovery images differ from the current source/run/attempt")
    refs = {}
    for role, name in RECOVERY_IMAGES.items():
        ref = images[name]["ref"]
        _run(["docker", "pull", ref])
        inspected = _inspect_recovery_image(
            role, ref, revision=revision, upstream=upstream
        )
        if inspected["Id"] != images[name].get("id") or ref not in inspected.get(
            "RepoDigests", []
        ):
            raise FixtureError(
                "Restored recovery image content differs from its receipt"
            )
        refs[role] = ref
    return refs


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
    for action in ("save-recovery", "restore-recovery"):
        command = commands.add_parser(action)
        for flag in ("registry", "state"):
            command.add_argument("--" + flag, type=Path, required=True)
        for flag in ("revision", "upstream", "run-id", "attempt"):
            command.add_argument("--" + flag, required=True)
    commands.choices["restore-recovery"].add_argument("--artifact")
    args = parser.parse_args(argv)
    try:
        if args.command == "describe":
            print(json.dumps(describe(args.archive)))
        elif args.command == "restore":
            restore(args.archive, args.image, args.state, args.owner)
        elif args.command == "save-recovery":
            save_recovery(
                args.registry,
                args.state,
                revision=args.revision,
                upstream=args.upstream,
                run_id=args.run_id,
                attempt=args.attempt,
            )
        elif args.command == "restore-recovery":
            refs = restore_recovery(
                args.registry,
                args.state,
                revision=args.revision,
                upstream=args.upstream,
                run_id=args.run_id,
                attempt=args.attempt,
                artifact=args.artifact,
            )
            for role, ref in refs.items():
                print(f"{role}={ref}")
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
