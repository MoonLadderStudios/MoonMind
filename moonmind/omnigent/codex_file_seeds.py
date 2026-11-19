"""Bounded read-only capture of selected nonsecret Codex enrollment files.

The credential owner supplies the selected volume/image and persists the first
accepted result. This reader never selects credentials or grants retry authority.
"""

from __future__ import annotations

import asyncio
import json
import re
import tomllib
import uuid
from pathlib import PurePosixPath

from moonmind.provider_profiles.codex_configuration import (
    _validate_additional_file_path,
    _has_authority,
)
from moonmind.provider_profiles.volume_mounts import credential_volume_mount

MAX_FILES = 32
MAX_BYTES = 65536
OUTPUT_LIMIT = MAX_BYTES * 6 + 16384


class CodexFileSeedUnavailable(ValueError):
    """Selected seed cannot be carried safely; never contains source bytes."""


def validate_seed_paths(paths):
    if not isinstance(paths, list) or len(paths) > MAX_FILES:
        raise CodexFileSeedUnavailable("Codex seed file count is unsupported")
    result = []
    for value in paths:
        if not isinstance(value, str) or len(value) > 256:
            raise CodexFileSeedUnavailable("Codex seed path is unsupported")
        path = PurePosixPath(value)
        try:
            _validate_additional_file_path(path)
        except ValueError:
            raise CodexFileSeedUnavailable("Codex seed path is protected") from None
        if str(path) != value or not (
            value in {"AGENTS.md", "AGENTS.override.md"}
            or len(path.parts) > 1
            and path.parts[0] == "rules"
            and path.suffix in {".md", ".txt", ".json", ".toml"}
            and all(
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) for part in path.parts
            )
        ):
            raise CodexFileSeedUnavailable("Codex seed path is unsupported")
        if any(
            path != PurePosixPath(other)
            and (
                path in PurePosixPath(other).parents
                or PurePosixPath(other) in path.parents
            )
            for other in result
        ):
            raise CodexFileSeedUnavailable("Codex seed paths overlap")
        if value not in result:
            result.append(value)
    return result


def required_seed_paths(configuration):
    seen, required = set(), []
    for item in configuration.get("additionalFiles") or []:
        prefix = "{{runtime_support_dir}}/"
        path = item["path"]
        if not isinstance(path, str) or not path.startswith(prefix):
            raise CodexFileSeedUnavailable("Codex seed destination is unsupported")
        relative = str(PurePosixPath(path[len(prefix) :]))
        if relative not in seen and item["merge_strategy"] != "replace":
            required.append(relative)
        seen.add(relative)
    return validate_seed_paths(required)


def validate_seed_capture(payload, paths):
    expected = validate_seed_paths(paths)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "files"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["files"], list)
        or len(payload["files"]) != len(expected)
    ):
        raise CodexFileSeedUnavailable("Codex seed receipt is malformed")
    total = 0
    for item, path in zip(payload["files"], expected):
        if (
            not isinstance(item, dict)
            or item.get("path") != path
            or type(item.get("present")) is not bool
        ):
            raise CodexFileSeedUnavailable("Codex seed receipt identity is malformed")
        keys = (
            {"path", "present", "content"} if item["present"] else {"path", "present"}
        )
        if set(item) != keys:
            raise CodexFileSeedUnavailable("Codex seed receipt fields are malformed")
        if item["present"]:
            content = item["content"]
            if not isinstance(content, str) or "\0" in content:
                raise CodexFileSeedUnavailable("Codex seed content is unsupported")
            try:
                total += len(content.encode("utf-8"))
            except UnicodeError:
                raise CodexFileSeedUnavailable(
                    "Codex seed encoding is unsupported"
                ) from None
            if total > MAX_BYTES:
                raise CodexFileSeedUnavailable("Codex seed content exceeds its bound")
            try:
                _scan_seed_data(content)
                if content.strip() and path.endswith((".json", ".toml")):
                    parsed = (
                        json.loads(content)
                        if path.endswith(".json")
                        else tomllib.loads(content)
                    )
                    if not isinstance(parsed, dict):
                        raise ValueError()
                    _scan_seed_data(parsed)
            except Exception:
                raise CodexFileSeedUnavailable(
                    "Codex seed content is unsafe or malformed"
                ) from None
    return payload


# Root is the single selected read-only mount. Traverse with directory descriptors
# and O_NOFOLLOW, not resolve-then-open, so path races cannot follow another file.
SEED_READ_SCRIPT = r"""
import errno, json, os, stat, sys
try:
    paths = json.load(sys.stdin)
    if not isinstance(paths, list) or len(paths) > 32:
        raise ValueError()
    root = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    result, total = [], 0
    try:
        for path in paths:
            parts = path.split('/')
            if not parts or any(not p or p in {'.', '..'} for p in parts):
                raise ValueError()
            parent = os.dup(root)
            try:
                try:
                    for part in parts[:-1]:
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        os.close(parent); parent = child
                    fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                except FileNotFoundError:
                    result.append({'path': path, 'present': False})
                    continue
                try:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 65536-total:
                        raise ValueError()
                    with os.fdopen(fd, 'rb', closefd=False) as stream:
                        data = stream.read(65537-total)
                    after = os.fstat(fd)
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError()
                    total += len(data)
                    if total > 65536:
                        raise ValueError()
                    result.append({'path': path, 'present': True, 'content': data.decode('utf-8')})
                finally:
                    os.close(fd)
            finally:
                os.close(parent)
    finally:
        os.close(root)
    print(json.dumps({'version': 1, 'files': result}))
except (ValueError, UnicodeError, TypeError):
    sys.exit(2)
except OSError as error:
    sys.exit(2 if error.errno in {errno.ELOOP, errno.ENOTDIR} else 3)
"""


async def _stop_reader(backend, name):
    code, stdout, stderr = await backend.run(
        ["docker", "rm", "--force", name],
        input_bytes=None,
        timeout_seconds=10,
        output_limit_bytes=1024,
    )
    if code and b"No such container" not in stderr + stdout:
        raise RuntimeError("Codex seed reader cleanup is unconfirmed")


def _scan_seed_data(value):
    from moonmind.omnigent.conformance import assert_secret_free

    assert_secret_free(value)
    if isinstance(value, dict):
        if _has_authority(dict.fromkeys(value)):
            raise ValueError("credential-bearing seed keys")
        for child in value.values():
            _scan_seed_data(child)
    elif isinstance(value, list):
        for child in value:
            _scan_seed_data(child)
    elif isinstance(value, str):
        # Markdown/text can contain JSON or quoted configuration examples.
        # Scan assignment keys independent of the file extension, including
        # escaped JSON key spellings, before any content becomes a receipt.
        keys = re.findall(r"(?im)([a-z_][a-z0-9_-]*)[\"']?\s*[:=]", value)
        for encoded in re.findall(r'("(?:[^"\\]|\\.)*")\s*:', value):
            try:
                keys.append(json.loads(encoded))
            except json.JSONDecodeError:
                continue
        for key in keys:
            if _has_authority({key: None}):
                raise ValueError("credential-bearing seed assignment")


async def capture_codex_file_seeds(backend, *, volume_ref, writer_image_ref, paths):
    paths = validate_seed_paths(paths)
    mount = credential_volume_mount(volume_ref, "/selected", read_only=True)
    if (
        not isinstance(writer_image_ref, str)
        or not writer_image_ref
        or writer_image_ref.startswith("-")
    ):
        raise CodexFileSeedUnavailable("Codex seed reader image is unavailable")
    name = "moonmind-codex-seeds-" + uuid.uuid4().hex
    started = False
    try:
        present, _stdout, _stderr = await backend.run(
            ["docker", "volume", "inspect", "--format", "{{.Name}}", volume_ref],
            input_bytes=None,
            timeout_seconds=30,
            output_limit_bytes=1024,
        )
        if present != 0:
            raise RuntimeError("selected enrollment volume is unavailable")
        started = True
        code, stdout, _stderr = await backend.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "-i",
                "--network",
                "none",
                "--read-only",
                "--mount",
                mount,
                "--entrypoint",
                "python3",
                writer_image_ref,
                "-c",
                SEED_READ_SCRIPT,
                "/selected",
            ],
            input_bytes=json.dumps(paths).encode(),
            timeout_seconds=30,
            output_limit_bytes=OUTPUT_LIMIT,
            binary_stdout_limit_bytes=OUTPUT_LIMIT,
        )
    except Exception:
        raise RuntimeError("Codex seed reader transport failed") from None
    finally:
        if started:
            cleanup = asyncio.create_task(_stop_reader(backend, name))
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            try:
                cleanup.result()
            except Exception:
                raise RuntimeError("Codex seed reader cleanup is unconfirmed") from None
            if cancelled:
                raise asyncio.CancelledError()
    if code == 2:
        raise CodexFileSeedUnavailable("Codex seed source is unsafe or unreadable")
    if code != 0:
        raise RuntimeError("Codex seed reader failed")
    try:
        if len(stdout) > OUTPUT_LIMIT:
            raise ValueError()
        return validate_seed_capture(json.loads(stdout), paths)
    except Exception:
        raise CodexFileSeedUnavailable(
            "Codex seed content is unsafe or malformed"
        ) from None
