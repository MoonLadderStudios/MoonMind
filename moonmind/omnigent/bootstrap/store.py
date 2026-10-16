"""Durable store for bootstrap and resolved image state."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moonmind.omnigent.bootstrap.models import (
    BootstrapRecord,
    ResolvedOmnigentDeploymentState,
)

_DEFAULT_BOOTSTRAP_PATH = Path(
    os.getenv("MOONMIND_OMNIGENT_BOOTSTRAP_STATE_PATH", "var/omnigent-runtime-state/bootstrap.json")
)
_DEFAULT_RESOLVED_PATH = Path(
    os.getenv("MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", "var/omnigent-runtime-state/resolved-images.json")
)

# Alternative mounted path for compose
_COMPOSE_BOOTSTRAP = Path("/app/var/omnigent-runtime-state/bootstrap.json")
_COMPOSE_RESOLVED = Path("/app/var/omnigent-runtime-state/resolved-images.json")


def _candidate_paths(default: Path, compose: Path) -> list[Path]:
    # Prefer explicit env, then compose mount, then default repo path
    env_path = os.getenv("MOONMIND_OMNIGENT_BOOTSTRAP_STATE_PATH" if "bootstrap" in str(default) else "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", "").strip()
    if env_path:
        return [Path(env_path)]
    # Try both compose and default
    return [compose, default]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_bootstrap_record() -> BootstrapRecord | None:
    for p in _candidate_paths(_DEFAULT_BOOTSTRAP_PATH, _COMPOSE_BOOTSTRAP):
        data = _read_json(p)
        if data is not None:
            try:
                return BootstrapRecord.model_validate(data)
            except Exception:
                continue
    return None


def save_bootstrap_record(record: BootstrapRecord) -> None:
    payload = record.model_dump(mode="json", by_alias=True)
    for p in _candidate_paths(_DEFAULT_BOOTSTRAP_PATH, _COMPOSE_BOOTSTRAP):
        try:
            _write_json(p, payload)
            break
        except OSError:
            continue


def load_resolved_state() -> ResolvedOmnigentDeploymentState | None:
    for p in _candidate_paths(_DEFAULT_RESOLVED_PATH, _COMPOSE_RESOLVED):
        data = _read_json(p)
        if data is not None:
            try:
                return ResolvedOmnigentDeploymentState.model_validate(data)
            except Exception:
                continue
    # Try env fallback: if image refs already set in env, synthesize
    server_ref = os.getenv("OMNIGENT_IMAGE_REF", "").strip()
    host_ref = os.getenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "").strip()
    server_digest = re.fullmatch(r".+@(sha256:[0-9a-f]{64})", server_ref)
    build_digest = server_digest.group(1) if server_digest else ""
    shared_ref = os.getenv("OMNIGENT_SHARED_HOST_IMAGE_REF", "").strip()
    if server_ref or host_ref or shared_ref:
        try:
            return ResolvedOmnigentDeploymentState(
                serverImageRef=server_ref or None,
                opencodeHostImageRef=host_ref or None,
                sharedHostImageRef=shared_ref or None,
                omnigentBuildDigest=build_digest or None,
                resolvedAt=datetime.now(UTC),
                source="env",
            )
        except Exception:
            return None
    return None


def save_resolved_state(state: ResolvedOmnigentDeploymentState) -> None:
    payload = state.model_dump(mode="json", by_alias=True)
    for p in _candidate_paths(_DEFAULT_RESOLVED_PATH, _COMPOSE_RESOLVED):
        try:
            _write_json(p, payload)
            break
        except OSError:
            continue


# Release-owned image env keys and the resolved-state fields that observe them.
_INSTALLED_IMAGE_FIELDS = (
    ("OMNIGENT_IMAGE_REF", "server_image_ref"),
    ("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "opencode_host_image_ref"),
    ("OMNIGENT_SHARED_HOST_IMAGE_REF", "shared_host_image_ref"),
    ("OMNIGENT_PI_HOST_IMAGE_REF", "pi_host_image_ref"),
)
_DIGEST_PINNED_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


def with_installed_image_refs(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return ``environment`` with the installed Omnigent images applied.

    Compose copies the release's image refs into a service's environment only
    when it creates that service, so a long-lived API or worker keeps the
    refs of whichever release was current at its start. The persisted
    resolved state is what the release and the API image leg last verified,
    and it already carries any explicit operator pin, so it takes precedence;
    the environment is only the fallback where nothing has been resolved.
    """

    merged = dict(os.environ if environment is None else environment)
    try:
        state = load_resolved_state()
    except Exception:
        state = None
    if state is None:
        return merged
    for key, attribute in _INSTALLED_IMAGE_FIELDS:
        value = str(getattr(state, attribute, "") or "").strip()
        if _DIGEST_PINNED_RE.fullmatch(value):
            merged[key] = value
    return merged


def load_installed_release_image_env() -> dict[str, str]:
    """Return the image refs of the Omnigent release the deployment installed.

    The release controller records one release in the desired-state sidecar
    (``omnigentRelease``, written by
    ``moonmind.workflows.skills.omnigent_release``) and renders the same refs
    into ``.env.deploy``. Reading the record keeps image resolution on the
    installed release in a process that outlived the release, instead of
    resolving the refs it inherited when it started.
    """

    path = os.getenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", "").strip()
    if not path:
        return {}
    document = _read_json(Path(path))
    record = document.get("omnigentRelease") if isinstance(document, dict) else None
    if not isinstance(record, dict):
        return {}
    server = str(record.get("serverImageRef") or "").strip()
    hosts = record.get("hostImageRefs")
    if not _DIGEST_PINNED_RE.fullmatch(server) or not isinstance(hosts, dict):
        return {}
    refs = {"OMNIGENT_IMAGE_REF": server}
    for kind, key in (
        ("codex", "OMNIGENT_HOST_IMAGE_REF"),
        ("opencode", "OMNIGENT_OPENCODE_HOST_IMAGE_REF"),
        ("shared", "OMNIGENT_SHARED_HOST_IMAGE_REF"),
        ("pi", "OMNIGENT_PI_HOST_IMAGE_REF"),
    ):
        value = str(hosts.get(kind) or "").strip()
        if _DIGEST_PINNED_RE.fullmatch(value):
            refs[key] = value
    return refs
