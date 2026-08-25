"""Automatic immutable image resolution for Omnigent deployment."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime

from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState

_DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


async def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, "", "timeout"
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


def _is_digest_pinned(ref: str) -> bool:
    return bool(_DIGEST_RE.fullmatch(ref.strip()))


def _extract_digest(ref: str) -> str | None:
    if "@sha256:" in ref:
        digest = ref.rsplit("@sha256:", 1)[-1]
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest.lower()):
            return "sha256:" + digest.lower()
    return None


async def _resolve_via_docker_inspect(image: str) -> str | None:
    # Try docker image inspect to get RepoDigests
    code, out, _ = await _run(["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"])
    if code != 0:
        return None
    try:
        digests = json.loads(out.strip())
        if isinstance(digests, list) and digests:
            # Prefer first that looks like digest-pinned
            for d in digests:
                if _is_digest_pinned(str(d)):
                    return str(d)
            # If none pinned, construct from Id?
            return str(digests[0])
    except json.JSONDecodeError:
        # Best-effort: docker inspect output was not JSON; treat as unresolved
        pass
    return None


async def _resolve_via_docker_pull(image: str, tag: str) -> str | None:
    # Try docker pull
    ref = f"{image}:{tag}"
    code, out, err = await _run(["docker", "pull", ref], timeout=120)
    if code != 0:
        # Try without explicit tag if image already includes tag?
        return None
    # After pull, inspect
    return await _resolve_via_docker_inspect(ref)


async def _resolve_image(image_env: str, tag_env: str, ref_env: str) -> tuple[str | None, str | None]:
    """Resolve one image to digest-pinned ref. Returns (pinned_ref, build_digest)."""
    pinned = os.getenv(ref_env, "").strip()
    if pinned and _is_digest_pinned(pinned) and not pinned.endswith("0" * 64) and not pinned.endswith("c" * 64):
        # Valid pinned ref
        build_digest = _extract_digest(pinned)
        return pinned, build_digest
    # Need to resolve from image+tag
    image = os.getenv(image_env, "").strip()
    tag = os.getenv(tag_env, "").strip() or "latest"
    if not image:
        return None, None
    # Try inspect existing local image without pull
    candidate = f"{image}:{tag}"
    resolved = await _resolve_via_docker_inspect(candidate)
    if resolved and _is_digest_pinned(resolved):
        # Ensure we return canonical repo@digest, not just digest from inspect which may be repo@sha256
        return resolved, _extract_digest(resolved)
    # Try pull
    pulled = await _resolve_via_docker_pull(image, tag)
    if pulled and _is_digest_pinned(pulled):
        return pulled, _extract_digest(pulled)
    # Fallback: try docker images --digests
    code, out, _ = await _run(["docker", "images", "--digests", "--format", "{{.Repository}}:{{.Tag}}@{{.Digest}} {{.ID}}"])
    if code == 0:
        for line in out.splitlines():
            part = line.strip().split()[0] if line.strip() else ""
            if part.startswith(image) and _is_digest_pinned(part):
                return part, _extract_digest(part)
    return None, None


async def resolve_omnigent_images() -> ResolvedOmnigentDeploymentState:
    """Resolve server and OpenCode host images to immutable digests.

    This is best-effort: if docker is unavailable or images cannot be pulled,
    returns whatever is configured via env or previously persisted state.
    """
    from moonmind.omnigent.bootstrap.store import load_resolved_state

    previous = load_resolved_state()

    # Server image
    server_ref, server_digest = await _resolve_image(
        "OMNIGENT_IMAGE", "OMNIGENT_IMAGE_TAG", "OMNIGENT_IMAGE_REF"
    )
    # Also check OMNIGENT_BUILD_DIGEST directly
    build_digest = os.getenv("OMNIGENT_BUILD_DIGEST", "").strip()
    if build_digest and _SHA256_RE.fullmatch(build_digest):
        server_digest = build_digest
    elif server_ref:
        server_digest = server_digest or _extract_digest(server_ref)

    # OpenCode host image
    opencode_ref, _ = await _resolve_image(
        "OMNIGENT_OPENCODE_HOST_IMAGE",
        "OMNIGENT_OPENCODE_HOST_IMAGE_TAG",
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
    )

    # Pi host (optional)
    pi_ref, _ = await _resolve_image(
        "OMNIGENT_PI_HOST_IMAGE", "OMNIGENT_PI_HOST_IMAGE_TAG", "OMNIGENT_PI_HOST_IMAGE_REF"
    )
    # Fall back to previous if still None
    if not server_ref and previous and previous.server_image_ref:
        server_ref = previous.server_image_ref
        server_digest = previous.omnigent_build_digest or server_digest
    if not opencode_ref and previous and previous.opencode_host_image_ref:
        opencode_ref = previous.opencode_host_image_ref
    if not pi_ref and previous and previous.pi_host_image_ref:
        pi_ref = previous.pi_host_image_ref

    # If we have pulled digests via alternative method, ensure fallback for build digest from RepoDigests
    if not server_digest and server_ref:
        server_digest = _extract_digest(server_ref)
    # Try to derive build digest from server image inspect if still missing
    if not server_digest:
        # Try to inspect omnigent server container's image
        code, out, _ = await _run(["docker", "inspect", "--format", "{{.Image}}", "moonmind-omnigent-1"])
        if code == 0:
            candidate = out.strip()
            if _SHA256_RE.fullmatch(candidate):
                server_digest = candidate

    # Architecture detection
    arch = "linux/amd64"
    # Try docker inspect for architecture
    target = opencode_ref or server_ref
    if target:
        code, out, _ = await _run(["docker", "image", "inspect", target, "--format", "{{.Architecture}}"])
        if code == 0 and out.strip():
            reported = out.strip().lower()
            if reported in {"amd64", "arm64", "arm"}:
                arch = f"linux/{reported}"
            elif "/" in reported:
                arch = reported

    state = ResolvedOmnigentDeploymentState(
        serverImageRef=server_ref,
        opencodeHostImageRef=opencode_ref,
        piHostImageRef=pi_ref,
        omnigentBuildDigest=server_digest,
        architecture=arch,
        resolvedAt=datetime.now(UTC),
        source="auto",
    )
    return state


async def publish_resolved_omnigent_images() -> ResolvedOmnigentDeploymentState:
    """Resolve, persist, and export the deployment's immutable image identities.

    Host Class selection, launch policy compilation, and Provider Profile
    runtime validation all read the digest-pinned refs straight from the process
    environment, and the canonical Compose path leaves
    ``OMNIGENT_OPENCODE_HOST_IMAGE_REF`` unset so the deployment can resolve its
    own digests. This is the single boundary that turns the configured
    image/tag into those exported digests, so every caller observes one
    authority instead of resolving separately.
    """

    from moonmind.omnigent.bootstrap.store import save_resolved_state

    state = await resolve_omnigent_images()
    save_resolved_state(state)
    exported = {
        "OMNIGENT_IMAGE_REF": state.server_image_ref,
        "OMNIGENT_BUILD_DIGEST": state.omnigent_build_digest,
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF": state.opencode_host_image_ref,
        "OMNIGENT_PI_HOST_IMAGE_REF": state.pi_host_image_ref,
    }
    for key, value in exported.items():
        cleaned = str(value or "").strip()
        if cleaned:
            os.environ[key] = cleaned
    return state


def resolved_server_image_ref(state: ResolvedOmnigentDeploymentState | None) -> str:
    if state and state.server_image_ref:
        return state.server_image_ref
    return os.getenv("OMNIGENT_IMAGE_REF", "").strip()


def resolved_opencode_image_ref(state: ResolvedOmnigentDeploymentState | None) -> str:
    if state and state.opencode_host_image_ref:
        return state.opencode_host_image_ref
    return os.getenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "").strip()


def resolved_build_digest(state: ResolvedOmnigentDeploymentState | None) -> str:
    if state and state.omnigent_build_digest:
        return state.omnigent_build_digest
    bd = os.getenv("OMNIGENT_BUILD_DIGEST", "").strip()
    if bd and _SHA256_RE.fullmatch(bd):
        return bd
    ref = resolved_server_image_ref(state)
    if ref:
        d = _extract_digest(ref)
        if d:
            return d
    return ""
