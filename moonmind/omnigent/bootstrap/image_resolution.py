"""Automatic immutable image resolution for Omnigent deployment."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime

from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState

_DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_OMNIGENT_VERSION_RE = re.compile(r"\bomnigent\s+([0-9]+\.[0-9]+\.[0-9]+)\b")


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
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _is_digest_pinned(ref: str) -> bool:
    return bool(_DIGEST_RE.fullmatch(ref.strip()))


def _extract_digest(ref: str) -> str | None:
    if "@sha256:" in ref:
        digest = ref.rsplit("@sha256:", 1)[-1]
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest.lower()):
            return "sha256:" + digest.lower()
    return None


def _image_repository(image: str) -> str:
    """Return the repository portion of an image tag or digest reference."""

    candidate = image.strip().split("@", 1)[0]
    slash = candidate.rfind("/")
    colon = candidate.rfind(":")
    return candidate[:colon] if colon > slash else candidate


async def _resolve_via_docker_inspect(
    image: str, *, inspect_ref: str | None = None
) -> str | None:
    # Try docker image inspect to get RepoDigests
    code, out, _ = await _run(
        [
            "docker",
            "image",
            "inspect",
            inspect_ref or image,
            "--format",
            "{{json .RepoDigests}}",
        ]
    )
    if code != 0:
        return None
    try:
        digests = json.loads(out.strip())
        if isinstance(digests, list) and digests:
            repository = _image_repository(image)
            for d in digests:
                candidate = str(d)
                if (
                    _is_digest_pinned(candidate)
                    and _image_repository(candidate) == repository
                ):
                    return candidate
    except json.JSONDecodeError:
        # Best-effort: docker inspect output was not JSON; treat as unresolved
        pass
    return None


async def _resolve_running_server_image(
    image: str, env: Mapping[str, str]
) -> str | None:
    """Resolve the immutable image used by this deployment's live server."""

    project_name = (
        str(env.get("MOONMIND_DEPLOYMENT_PROJECT_NAME") or "moonmind").strip()
        or "moonmind"
    )
    code, out, _ = await _run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--filter",
            "label=com.docker.compose.service=omnigent",
            "--format",
            "{{.ID}}",
        ]
    )
    container_ids = [line.strip() for line in out.splitlines() if line.strip()]
    if code != 0 or len(container_ids) != 1:
        return None
    code, out, _ = await _run(
        ["docker", "inspect", "--format", "{{.Image}}", container_ids[0]]
    )
    image_id = out.strip()
    if code != 0 or not _SHA256_RE.fullmatch(image_id):
        return None
    return await _resolve_via_docker_inspect(image, inspect_ref=image_id)


async def _resolve_via_docker_pull(image: str, tag: str) -> str | None:
    # Try docker pull
    ref = f"{image}:{tag}"
    code, out, err = await _run(["docker", "pull", ref], timeout=120)
    if code != 0:
        # Try without explicit tag if image already includes tag?
        return None
    # After pull, inspect
    return await _resolve_via_docker_inspect(ref)


async def _resolve_image(
    image_env: str,
    tag_env: str,
    ref_env: str,
    env: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve one image to a digest-pinned ref and its image digest."""
    source = os.environ if env is None else env
    pinned = str(source.get(ref_env) or "").strip()
    if (
        pinned
        and _is_digest_pinned(pinned)
        and not pinned.endswith("0" * 64)
        and not pinned.endswith("c" * 64)
    ):
        # Valid pinned ref
        build_digest = _extract_digest(pinned)
        return pinned, build_digest
    # Need to resolve from image+tag
    image = str(source.get(image_env) or "").strip()
    tag = str(source.get(tag_env) or "").strip() or "latest"
    if not image:
        return None, None
    # Mutable coordinates are refreshable deployment input, not launch
    # authority. Ask the registry first on every reconciliation pass so a
    # newly published default host replaces a cached stale tag. If the registry
    # is temporarily unavailable, the last locally resolved image remains a
    # bounded degraded fallback.
    candidate = f"{image}:{tag}"
    pulled = await _resolve_via_docker_pull(image, tag)
    if pulled and _is_digest_pinned(pulled):
        return pulled, _extract_digest(pulled)
    resolved = await _resolve_via_docker_inspect(candidate)
    if resolved and _is_digest_pinned(resolved):
        return resolved, _extract_digest(resolved)
    # Fallback: try docker images --digests
    code, out, _ = await _run(
        [
            "docker",
            "images",
            "--digests",
            "--format",
            "{{.Repository}}:{{.Tag}}@{{.Digest}} {{.ID}}",
        ]
    )
    if code == 0:
        for line in out.splitlines():
            part = line.strip().split()[0] if line.strip() else ""
            if part.startswith(image) and _is_digest_pinned(part):
                return part, _extract_digest(part)
    return None, None


async def _image_build_identity(image_ref: str) -> str | None:
    """Read the portable Omnigent build identity embedded in a host image.

    A repository manifest digest identifies the container image, not the
    Omnigent build shared by the server and host. Harness-specific host images
    publish that separate identity in a required OCI label. Pull an explicitly
    pinned image once when necessary so operator pins and mutable-tag defaults
    exercise the same inspection path.
    """

    async def inspect() -> str | None:
        code, out, _ = await _run(
            [
                "docker",
                "image",
                "inspect",
                image_ref,
                "--format",
                "{{json .Config.Labels}}",
            ]
        )
        if code != 0:
            return None
        try:
            labels = json.loads(out.strip())
        except json.JSONDecodeError:
            return None
        if not isinstance(labels, Mapping):
            return None
        candidate = str(labels.get("moonmind.omnigent.build_digest") or "").strip()
        return candidate if _SHA256_RE.fullmatch(candidate) else None

    observed = await inspect()
    if observed:
        return observed
    code, _, _ = await _run(["docker", "pull", image_ref], timeout=120)
    if code != 0:
        return None
    return await inspect()


async def _image_omnigent_version(image_ref: str) -> str | None:
    """Read the executable Omnigent version from one immutable image.

    The portable build label is release authority, but it is still metadata.
    Probing the binary at this trusted image-resolution boundary prevents a
    stale or incorrectly labelled runtime pack from becoming launch authority
    and defers the more expensive exact-host check to defense in depth.
    """

    code, stdout, _ = await _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/opt/venv/bin/omnigent",
            image_ref,
            "--version",
        ],
        timeout=90,
    )
    if code != 0:
        return None
    match = _OMNIGENT_VERSION_RE.search(stdout.strip())
    return match.group(1) if match is not None else None


async def resolve_omnigent_images(
    env: Mapping[str, str] | None = None,
) -> ResolvedOmnigentDeploymentState:
    """Resolve server and OpenCode host images to immutable digests.

    This is best-effort: if docker is unavailable or images cannot be pulled,
    returns whatever is configured via env or previously persisted state.

    ``env`` exists so callers can resolve against the deployment's own image
    configuration rather than a digest a previous pass published. Resolving
    against a self-published digest would make every configured mutable tag look
    explicitly pinned and silently disable tag refresh.
    """
    from moonmind.omnigent.bootstrap.store import load_resolved_state

    source = os.environ if env is None else env
    previous = load_resolved_state()

    # A configured digest is direct authority. For the default mutable-tag
    # path, compatibility must describe the image the Compose service is
    # actually running, not a newer image that a reconciliation pull merely
    # placed in the local cache.
    configured_server_ref = str(source.get("OMNIGENT_IMAGE_REF") or "").strip()
    configured_server_image = str(source.get("OMNIGENT_IMAGE") or "").strip()
    server_requires_live_evidence = bool(
        not configured_server_ref and configured_server_image
    )
    running_server_ref = None
    if server_requires_live_evidence:
        running_server_ref = await _resolve_running_server_image(
            configured_server_image, source
        )
    if running_server_ref:
        server_ref = running_server_ref
        server_image_digest = _extract_digest(running_server_ref)
    elif server_requires_live_evidence:
        # A registry digest or an old persisted ref cannot prove which mutable
        # tag Compose is serving. Leave server authority unavailable so the
        # compatibility gate retries instead of admitting an unverified pair.
        server_ref = None
        server_image_digest = None
    else:
        server_ref, server_image_digest = await _resolve_image(
            "OMNIGENT_IMAGE", "OMNIGENT_IMAGE_TAG", "OMNIGENT_IMAGE_REF", source
        )

    # OpenCode host image
    opencode_ref, _ = await _resolve_image(
        "OMNIGENT_OPENCODE_HOST_IMAGE",
        "OMNIGENT_OPENCODE_HOST_IMAGE_TAG",
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        source,
    )

    # Pi host (optional)
    pi_ref, _ = await _resolve_image(
        "OMNIGENT_PI_HOST_IMAGE",
        "OMNIGENT_PI_HOST_IMAGE_TAG",
        "OMNIGENT_PI_HOST_IMAGE_REF",
        source,
    )
    # Shared neutral host image (optional until Codex/Claude Host Classes are
    # selected; resolution stays best-effort so OpenCode-only deployments do
    # not require it).
    shared_ref, _ = await _resolve_image(
        "OMNIGENT_SHARED_HOST_IMAGE",
        "OMNIGENT_SHARED_HOST_IMAGE_TAG",
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        source,
    )
    # Fall back to previous if still None
    if (
        not server_requires_live_evidence
        and not server_ref
        and previous
        and previous.server_image_ref
    ):
        server_ref = previous.server_image_ref
        server_image_digest = _extract_digest(server_ref)
    if not opencode_ref and previous and previous.opencode_host_image_ref:
        opencode_ref = previous.opencode_host_image_ref
    if not pi_ref and previous and previous.pi_host_image_ref:
        pi_ref = previous.pi_host_image_ref
    if not shared_ref and previous and previous.shared_host_image_ref:
        shared_ref = previous.shared_host_image_ref

    # The default release embeds the exact server repository digest as its
    # paired runtime-pack identity. It is distinct from the custom host image's
    # own repository digest. An operator may instead provide an explicit shared
    # build identity for an independently paired server and host.
    if not server_image_digest and server_ref:
        server_image_digest = _extract_digest(server_ref)
    configured_build_digest = str(source.get("OMNIGENT_BUILD_DIGEST") or "").strip()
    if configured_build_digest and not _SHA256_RE.fullmatch(configured_build_digest):
        raise ValueError("OMNIGENT_BUILD_DIGEST must be an exact sha256 identity")
    host_build_digest = (
        await _image_build_identity(opencode_ref) if opencode_ref else None
    )
    server_version = (
        await _image_omnigent_version(server_ref)
        if server_ref and opencode_ref
        else None
    )
    host_version = await _image_omnigent_version(opencode_ref) if opencode_ref else None
    compatibility_failure: str | None = None
    if opencode_ref:
        if not server_ref or not server_image_digest:
            compatibility_failure = "omnigent_server_build_unavailable"
        elif not host_build_digest:
            compatibility_failure = "omnigent_host_build_identity_unavailable"
        elif configured_build_digest and configured_build_digest != host_build_digest:
            compatibility_failure = "omnigent_operator_host_build_mismatch"
        elif not configured_build_digest and server_image_digest != host_build_digest:
            compatibility_failure = "omnigent_server_host_build_mismatch"
        elif server_version is None or host_version is None:
            compatibility_failure = "omnigent_server_host_version_probe_failed"
        elif server_version != host_version:
            compatibility_failure = "omnigent_server_host_version_mismatch"
        elif not await _image_opencode_bootstrap_ready(opencode_ref):
            compatibility_failure = "omnigent_host_bootstrap_contract_missing"

    if compatibility_failure:
        # Keep the current server as catalog authority while quarantining the
        # incompatible runtime pack. The selector consumes this same verdict,
        # so existing signed qualification evidence cannot launch the stale
        # image while the registry catches up.
        omnigent_build_digest = configured_build_digest or server_image_digest
        build_identity_source = (
            "operator-quarantine"
            if configured_build_digest
            else "server-image-quarantine"
        )
    elif configured_build_digest:
        omnigent_build_digest = configured_build_digest
        build_identity_source = "operator"
    elif host_build_digest:
        omnigent_build_digest = host_build_digest
        build_identity_source = "opencode-host-label"
    elif previous and previous.omnigent_build_digest:
        omnigent_build_digest = previous.omnigent_build_digest
        build_identity_source = "persisted"
    else:
        # Legacy Codex-only deployments do not select the OpenCode Host Class.
        omnigent_build_digest = server_image_digest
        build_identity_source = "server-image-digest"

    # Architecture detection
    arch = "linux/amd64"
    # Try docker inspect for architecture
    target = opencode_ref or server_ref
    if target:
        code, out, _ = await _run(
            ["docker", "image", "inspect", target, "--format", "{{.Architecture}}"]
        )
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
        sharedHostImageRef=shared_ref,
        omnigentBuildDigest=omnigent_build_digest,
        architecture=arch,
        resolvedAt=datetime.now(UTC),
        source="auto",
        details={
            "serverImageDigest": server_image_digest,
            "buildIdentitySource": build_identity_source,
            "opencodeHostCompatibility": {
                "status": "blocked" if compatibility_failure else "ready",
                "failureCode": compatibility_failure,
                "serverImageRef": server_ref,
                "hostImageRef": opencode_ref,
                "serverBuildDigest": server_image_digest,
                "hostBuildDigest": host_build_digest,
                "serverVersion": server_version,
                "hostVersion": host_version,
            },
        },
    )
    return state


async def _image_opencode_bootstrap_ready(image_ref: str) -> bool:
    """Verify startup prerequisites in the selected image, without credentials.

    Labels and matching Omnigent versions cannot attest the derived image's
    contents. The entrypoint requires this cache before it can register a host.
    Image publication additionally exercises plugin-enabled startup offline.
    """
    from moonmind.omnigent.host_services.runtime_scripts import (
        _OPENCODE_PLUGIN_NPM_CACHE_SEED,
    )

    code, _, _ = await _run(
        [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--entrypoint", "/bin/sh", image_ref, "-ec",
            f"test -d {_OPENCODE_PLUGIN_NPM_CACHE_SEED}/_cacache "
            "&& test -x /usr/local/bin/opencode",
        ],
        timeout=30,
    )
    return code == 0


# The digests published below are written back into the process environment so
# every selector observes one authority. That export must never become an input
# to resolution or to registry acquisition: a self-published digest is
# indistinguishable from an operator pin, and treating it as one would disable
# refresh for every configured mutable tag. These are the keys publication
# writes, captured once at their operator-supplied values.
_PUBLISHED_IMAGE_KEYS = (
    "OMNIGENT_IMAGE_REF",
    "OMNIGENT_BUILD_DIGEST",
    "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
    "OMNIGENT_PI_HOST_IMAGE_REF",
    "OMNIGENT_SHARED_HOST_IMAGE_REF",
)
_operator_image_baseline: dict[str, str] | None = None


def operator_image_configuration(
    *, env: Mapping[str, str] | None = None
) -> Mapping[str, str]:
    """Return the deployment's own image configuration, free of published digests.

    Callers that resolve or acquire images must read this instead of the live
    environment so tag refresh keeps working across passes. Callers that only
    consume an already-resolved identity should keep reading the environment.
    """

    global _operator_image_baseline

    source = os.environ if env is None else env
    if _operator_image_baseline is None:
        _operator_image_baseline = {
            key: str(source.get(key) or "").strip() for key in _PUBLISHED_IMAGE_KEYS
        }
    merged = dict(source)
    for key, value in _operator_image_baseline.items():
        if value:
            merged[key] = value
        else:
            merged.pop(key, None)
    return merged


def reset_operator_image_configuration() -> None:
    """Forget the captured baseline (tests only)."""

    global _operator_image_baseline
    _operator_image_baseline = None


async def publish_resolved_omnigent_images() -> ResolvedOmnigentDeploymentState:
    """Resolve, persist, and export the deployment's immutable image identities.

    Host Class selection, launch policy compilation, and Provider Profile
    runtime validation all read the digest-pinned refs straight from the process
    environment, and the canonical Compose path leaves
    ``OMNIGENT_OPENCODE_HOST_IMAGE_REF`` unset so the deployment can resolve its
    own digests. This is the single boundary that turns the configured
    image/tag into those exported digests, so every caller observes one
    authority instead of resolving separately.

    Resolution always reads :func:`operator_image_configuration`, never the
    digests this function exports, so a configured mutable tag stays refreshable
    on every pass.
    """

    from moonmind.omnigent.bootstrap.store import save_resolved_state

    state = await resolve_omnigent_images(operator_image_configuration())
    save_resolved_state(state)
    exported = {
        "OMNIGENT_IMAGE_REF": state.server_image_ref,
        "OMNIGENT_BUILD_DIGEST": state.omnigent_build_digest,
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF": state.opencode_host_image_ref,
        "OMNIGENT_PI_HOST_IMAGE_REF": state.pi_host_image_ref,
        "OMNIGENT_SHARED_HOST_IMAGE_REF": state.shared_host_image_ref,
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
