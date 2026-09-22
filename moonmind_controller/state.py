"""One small local operation record for the standalone controller.

Stdlib-only. The record distinguishes desired state (what the operator
requested, including selected concrete images) from installed state (what
apply actually confirmed). Writes are crash-safe: interruption leaves the
previous complete record, never a truncated sole copy.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SCHEMA = "moonmind-controller-operation/v1"

# Bounded automatic attempts for one operation. An explicit Retry starts a
# fresh bounded attempt with prior diagnostics retained.
MAX_ATTEMPTS = 3


def _path(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)


def new_operation(*, operation_id: str, target_image: str) -> dict:
    """Create the desired-state record for a requested update."""
    return {
        "schema": SCHEMA,
        "operationId": operation_id,
        "status": "desired",
        "attempt": 0,
        "desired": {"targetImage": target_image},
        "installed": None,
        "attempts": [],
    }


def read_record(path: str | Path) -> dict:
    """Read an operation record; a corrupt file is explicit, not success."""
    raw = _path(path).read_text()
    record = json.loads(raw)
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise ValueError("Operation record has an unrecognized schema")
    return record


def write_record(path: str | Path, record: dict) -> None:
    """Persist a complete record atomically across interruption."""
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(record, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reserve_record(path: str | Path, record: dict) -> dict:
    """Publish without replacing another writer's decision (first wins)."""
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return read_record(target)
    write_record(target, record)
    return read_record(target)


def record_concrete_images(record: dict, images: dict[str, str]) -> dict:
    """Record selected concrete images once, before apply recreates anything."""
    updated = dict(record)
    desired = dict(record.get("desired") or {})
    if "concreteImages" not in desired:
        desired["concreteImages"] = dict(images)
        updated["desired"] = desired
    return updated


def mark_installed(record: dict, *, installed_image: str, service_images: dict) -> dict:
    """Confirm installation; only observed apply results reach this state."""
    updated = dict(record)
    updated["installed"] = {"image": installed_image, "services": dict(service_images)}
    updated["status"] = "installed"
    return updated


def apply_already_complete(record: dict) -> bool:
    """A lost result must not repeat a completed apply unnecessarily."""
    installed = record.get("installed")
    desired_image = (record.get("desired") or {}).get("targetImage")
    status = record.get("status")
    if status == "installed" and isinstance(installed, dict):
        return installed.get("image") == desired_image
    # A desired record whose installed image already matches needs no apply.
    if isinstance(installed, dict) and installed.get("image") == desired_image:
        return True
    return False


def record_attempt_error(path: str | Path, *, attempt: int, error: str) -> list:
    """Keep every attempt's error so later noise cannot erase the first one."""
    record = read_record(path)
    history = list(record.get("attempts") or [])
    history.append({"attempt": attempt, "error": error})
    record["attempts"] = history
    record["attempt"] = attempt
    if record.get("status") != "installed":
        record["status"] = "failed" if attempt >= MAX_ATTEMPTS else "desired"
    write_record(path, record)
    return history


def explicit_retry(path: str | Path) -> dict:
    """Start a fresh bounded attempt; prior diagnostics are retained."""
    record = read_record(path)
    if record.get("status") == "installed":
        raise ValueError("A confirmed installation is not retried")
    record["attempt"] = int(record.get("attempt") or 0) + 1
    record["status"] = "desired"
    write_record(path, record)
    return record


# Prepared target + last installed configuration + compatibility-gated
# previous release. These are additive helpers for the apply orchestrator:
# the prepared target is written before any mutation, the installed config
# only after a verified apply, and the previous release is retained solely
# for restoration within actual compatibility (never an automatic DB
# downgrade).

def record_prepared_target(record: dict, *, target: dict) -> dict:
    """Persist the prepared target (concrete images + services) before apply."""
    updated = dict(record)
    prepared = dict(target or {})
    # Selected concrete images are recorded once; a prepared target never
    # rewrites an already-recorded selection for the same operation.
    desired = dict(record.get("desired") or {})
    existing_images = desired.get("concreteImages")
    if existing_images is not None and "concreteImages" not in prepared:
        prepared["concreteImages"] = dict(existing_images)
    updated["prepared"] = prepared
    return updated


def record_installed_config(record: dict, *, config: dict) -> dict:
    """Persist the last supported installed configuration after apply."""
    updated = dict(record)
    updated["installedConfig"] = dict(config or {})
    return updated


def record_previous_release(record: dict, *, previous: dict, compatible: bool) -> dict:
    """Retain the previous release only within actual compatibility.

    When ``compatible`` is False (schema/history mismatch) the previous
    release is not retained for restoration: restoring across an
    incompatible boundary would imply an automatic database downgrade,
    which the controller never performs.
    """
    updated = dict(record)
    if compatible:
        updated["previousRelease"] = dict(previous or {})
    else:
        updated.pop("previousRelease", None)
    updated["previousReleaseCompatible"] = bool(compatible)
    return updated
