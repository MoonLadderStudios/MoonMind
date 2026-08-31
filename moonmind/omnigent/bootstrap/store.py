"""Durable store for bootstrap and resolved image state."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moonmind.omnigent.bootstrap.models import BootstrapRecord, ResolvedOmnigentDeploymentState

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
    # also write to alternate location for visibility
    alt = _COMPOSE_RESOLVED if "resolved" in path.name else _COMPOSE_BOOTSTRAP
    if alt != path:
        try:
            alt.parent.mkdir(parents=True, exist_ok=True)
            alt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError:
            # Best-effort: alternate mount may be unavailable in hermetic tests
            pass


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
    runtime_ref = os.getenv("OMNIGENT_RUNTIME_HOST_IMAGE_REF", "").strip()
    build_digest = os.getenv("OMNIGENT_BUILD_DIGEST", "").strip()
    if server_ref or host_ref or runtime_ref or build_digest:
        try:
            return ResolvedOmnigentDeploymentState(
                serverImageRef=server_ref or None,
                opencodeHostImageRef=host_ref or None,
                runtimeHostImageRef=runtime_ref or None,
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
