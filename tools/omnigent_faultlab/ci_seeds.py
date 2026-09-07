"""Seed-range policy for the fault-lab generated corpus.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 8).

Required PR CI runs a fixed, deterministic bounded seed corpus so every pull
request pays the same predictable budget. A larger, *rotating* seed corpus runs
on main/schedule over a date-rotated window, so the generated interleaving space
keeps expanding beyond the fixed PR budget without slowing required PR CI.

The rotating window is selected by explicit, namespaced environment variables
that are set only in the scheduled/main CI context. When they are absent — every
pull request, every local run, every ad-hoc invocation — :func:`resolve_seed_corpus`
falls back to the fixed PR corpus. The default path (no env) therefore exercises
exactly the required-PR behavior; the rotating path is a pure superset selected by
explicit opt-in, never a hidden mode switch.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Fixed bounded corpus size that always runs in required PR CI.
PR_CI_SEED_COUNT = 400

#: Namespaced env vars that select the rotating window (set only on main/schedule).
ROTATING_ENABLED_ENV = "MOONMIND_FAULTLAB_ROTATING_SEEDS"
ROTATING_OFFSET_ENV = "MOONMIND_FAULTLAB_SEED_OFFSET"
ROTATING_COUNT_ENV = "MOONMIND_FAULTLAB_SEED_COUNT"

#: Rotating window size used when enabled without an explicit count budget.
DEFAULT_ROTATING_COUNT = 2000


def pr_ci_seeds() -> range:
    """The fixed, deterministic bounded corpus that runs in required PR CI."""

    return range(PR_CI_SEED_COUNT)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:  # fail fast; a malformed budget is not a silent 0.
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def rotating_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the rotating seed window is explicitly enabled by the environment."""

    env = os.environ if env is None else env
    return _truthy(env.get(ROTATING_ENABLED_ENV))


def rotating_seeds(env: Mapping[str, str] | None = None) -> range | None:
    """Resolve the rotating seed window from the environment.

    Returns ``None`` when rotating coverage is not enabled (the default for every
    PR and local run) so callers fall back to the fixed PR corpus. When enabled,
    returns ``range(offset, offset + count)`` with a bounded, non-negative window.
    A malformed or non-positive budget fails fast rather than silently degrading.
    """

    env = os.environ if env is None else env
    if not _truthy(env.get(ROTATING_ENABLED_ENV)):
        return None
    offset = _int_env(env, ROTATING_OFFSET_ENV, 0)
    count = _int_env(env, ROTATING_COUNT_ENV, DEFAULT_ROTATING_COUNT)
    if offset < 0:
        raise ValueError(f"{ROTATING_OFFSET_ENV} must be >= 0, got {offset}")
    if count <= 0:
        raise ValueError(f"{ROTATING_COUNT_ENV} must be > 0, got {count}")
    return range(offset, offset + count)


def resolve_seed_corpus(env: Mapping[str, str] | None = None) -> range:
    """The seed corpus to run: the rotating window when enabled, else PR fixed."""

    return rotating_seeds(env) or pr_ci_seeds()


__all__ = [
    "PR_CI_SEED_COUNT",
    "ROTATING_ENABLED_ENV",
    "ROTATING_OFFSET_ENV",
    "ROTATING_COUNT_ENV",
    "DEFAULT_ROTATING_COUNT",
    "pr_ci_seeds",
    "rotating_enabled",
    "rotating_seeds",
    "resolve_seed_corpus",
]
