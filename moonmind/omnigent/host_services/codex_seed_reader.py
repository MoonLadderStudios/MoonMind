"""Read-only selected-volume transport for bounded Codex seed capture.

Selection and content admission remain with the credential/configuration owner.
This adapter owns container presence, isolated execution and confirmed cleanup.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from moonmind.provider_profiles.volume_mounts import credential_volume_mount


async def _stop_reader(backend, name):
    code, stdout, stderr = await backend.run(
        ["docker", "rm", "--force", name],
        input_bytes=None,
        timeout_seconds=10,
        output_limit_bytes=1024,
    )
    if code and b"No such container" not in stderr + stdout:
        raise RuntimeError("Codex seed reader cleanup is unconfirmed")


async def read_selected_seed_files(
    backend, *, volume_ref, writer_image_ref, paths, script, output_limit
):
    mount = credential_volume_mount(volume_ref, "/selected", read_only=True)
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
                script,
                "/selected",
            ],
            input_bytes=json.dumps(paths).encode(),
            timeout_seconds=30,
            output_limit_bytes=output_limit,
            binary_stdout_limit_bytes=output_limit,
        )
    except TimeoutError:
        # The finally block must confirm daemon cleanup before this retryable
        # error escapes. Cleanup uncertainty replaces it with a hard failure.
        raise TimeoutError("Codex seed reader transport timed out") from None
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
    return code, stdout
