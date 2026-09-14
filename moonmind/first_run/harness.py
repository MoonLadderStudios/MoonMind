"""Disposable first-run harness for issue #3938.

The executable fresh-clone contract has two evidence tiers:

* **Required hermetic tier** (``integration`` + ``integration_ci``): covers the
  production startup/admission/host/session/finalization boundaries with
  controlled local dependencies only. It makes no live provider inference
  calls and never describes a stubbed run as proof of a live journey.
* **Protected live tier** (scheduled/manual only): exercises the exact default
  external provider and images from a documented clean state and publishes a
  live report. It is never part of required CI.

This module is deliberately dependency-light (stdlib only) so both tiers and
their tests stay deterministic and credential-free. It owns the shared
definitions — disposable-project rules, clean-install checks, failure
taxonomy, scoped teardown, redacted diagnostics, phase timings, and the live
report schema — while the live runner lives in
``tools/first_run_live_qualification.py``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# Ordered production journey phases. Timing is recorded per phase; the
# ten-minute goal is a *measured budget* under documented conditions, never a
# guarantee about arbitrary network speed or public-provider service.
FIRST_RUN_PHASES: tuple[str, ...] = (
    "image_acquisition",
    "bootstrap",
    "readiness",
    "admission",
    "provider_execution",
    "evidence_finalization",
    "cleanup",
)

TEN_MINUTE_BUDGET_SECONDS = 600

DEPLOYMENT_PROJECT_NAME = "moonmind"

_PROJECT_RE = re.compile(r"^moonmind-test(-[a-z0-9][a-z0-9_-]*)?$")

# Provider credentials that must never be inherited by a clean-install run.
# Presence of any of these (non-empty) fails the clean-install check.
FORBIDDEN_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENCODE_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_PAT",
)

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ghp_[A-Za-z0-9]+"), "ghp_[REDACTED]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]+"), "github_pat_[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9\-_]+"), "sk-[REDACTED]"),
    (re.compile(r"AIza[A-Za-z0-9\-_]+"), "AIza[REDACTED]"),
    (re.compile(r"AKIA[A-Z0-9]+"), "AKIA[REDACTED]"),
    (
        re.compile(r"(?i)(token|password|secret|api[_-]?key)\s*=\s*\S+"),
        r"\1=[REDACTED]",
    ),
)

DIAGNOSTIC_MAX_CHARS = 8000

# Failure taxonomy: provider failures are never platform success and never
# trigger substitution. Only explicit operator action may select a different
# provider, credential, or runtime.
_FAILURE_TAXONOMY: dict[str, dict[str, object]] = {
    "provider_unavailable": {
        "tier": "provider",
        "countable_as_success": False,
        "actionable": (
            "The default external provider is unreachable. Check network "
            "access to the provider, then retry the same bounded task. "
            "MoonMind does not switch providers automatically."
        ),
    },
    "provider_rate_limited": {
        "tier": "provider",
        "countable_as_success": False,
        "actionable": (
            "The default external provider rate-limited the request. Wait "
            "for the provider quota window, then retry the same bounded "
            "task. MoonMind does not switch providers automatically."
        ),
    },
    "image_resolution_failed": {
        "tier": "platform",
        "countable_as_success": False,
        "actionable": (
            "A required image reference could not be resolved. Verify the "
            "pinned digest and registry access, then retry. Startup cannot "
            "proceed without the exact image."
        ),
    },
    "bootstrap_authority_missing": {
        "tier": "platform",
        "countable_as_success": False,
        "actionable": (
            "Bootstrap authority (migration/seed evidence) is missing. "
            "Re-run bootstrap from the documented clean state; do not "
            "disable safety gates to make startup pass."
        ),
    },
    "startup_interrupted": {
        "tier": "platform",
        "countable_as_success": False,
        "actionable": (
            "Startup was interrupted before readiness. Restart the same "
            "disposable project; resume is idempotent and must not "
            "duplicate sessions, publications, or resources."
        ),
    },
    "worker_restart": {
        "tier": "platform",
        "countable_as_success": False,
        "actionable": (
            "A worker restarted mid-journey. The workflow resumes on the "
            "same session; retry is bounded and idempotent."
        ),
    },
    "startup_failure": {
        "tier": "platform",
        "countable_as_success": False,
        "actionable": (
            "MoonMind startup or admission failed before provider "
            "execution. Fix the platform cause (see diagnostics), then "
            "retry. This is distinct from a provider outage."
        ),
    },
    "admission_failure": {
        "tier": "platform",
        "countable_as_success": False,
        "actionable": (
            "Admission rejected the launch (no launch-ready profile or "
            "missing authority). Resolve the named authority and retry; "
            "no silent fallback profile is selected."
        ),
    },
}


def validate_disposable_project(project_name: str) -> None:
    """Enforce disposable-project naming; reject the deployment project.

    Raises ``ValueError`` for anything outside ``moonmind-test[-suffix]``,
    and always rejects the ``moonmind`` deployment project so a first-run
    attempt can never tear down operator resources.
    """
    if project_name == DEPLOYMENT_PROJECT_NAME or not _PROJECT_RE.match(
        project_name
    ):
        raise ValueError(
            f"first-run project must be 'moonmind-test' or "
            f"'moonmind-test-<suffix>', got {project_name!r}; the "
            f"'{DEPLOYMENT_PROJECT_NAME}' deployment project is never a "
            f"valid first-run target"
        )


def check_clean_install_env(
    env: dict[str, str | None],
    dot_env_exists: bool,
    catalog_rows_present: bool = False,
    profile_rows_present: bool = False,
) -> list[str]:
    """Return clean-install violations; empty means the starting state is clean.

    ``env`` is a mapping of environment values (``os.environ``-shaped). Any
    non-empty forbidden credential, an existing ``.env``, or prior catalog /
    profile state is a violation.
    """
    violations: list[str] = []
    for key in FORBIDDEN_ENV_KEYS:
        if (env.get(key) or "").strip():
            violations.append(f"inherited credential in environment: {key}")
    if dot_env_exists:
        violations.append(".env must not exist for the no-.env first-run path")
    if catalog_rows_present:
        violations.append("prior catalog state must be empty (clean volumes)")
    if profile_rows_present:
        violations.append("prior profile state must be empty (clean volumes)")
    return violations


def first_run_fixture() -> dict[str, object]:
    """Non-sensitive bounded fixture with explicit no-publication authority.

    The credentialless first-result scenario must not require a GitHub PAT,
    create public PRs, or mutate operator repositories.
    """
    return {
        "task": "Summarize this repository's README in three bullets.",
        "repository": None,
        "branch": None,
        "publication": {"mode": "none", "authorized": False},
        "requires_pat": False,
        "max_steps": 1,
    }


def resolve_same_authority(
    automatic: dict[str, object], explicit: dict[str, object]
) -> bool:
    """Omitted values and documented ``auto``/default equivalents must agree.

    Compares the intended authority refs, not incidental payload shape.
    """
    keys = ("profileId", "providerProfileRef", "launchPolicyRef")
    return all(automatic.get(k) == explicit.get(k) for k in keys)


def classify_first_run_failure(kind: str) -> dict[str, object]:
    """Classify a first-run failure into the provider-vs-platform taxonomy.

    Unknown kinds fail closed as platform failures. Provider failures are
    never countable as success and never authorize substitution.
    """
    record = _FAILURE_TAXONOMY.get(kind)
    if record is None:
        return {
            "kind": kind,
            "tier": "platform",
            "countable_as_success": False,
            "substitution_permitted": False,
            "actionable": (
                f"Unknown failure kind {kind!r}; treated as a platform "
                f"failure. Fix the named cause and retry without changing "
                f"provider, credential, or runtime."
            ),
        }
    return {
        "kind": kind,
        "tier": record["tier"],
        "countable_as_success": False,
        "substitution_permitted": False,
        "actionable": record["actionable"],
    }


@dataclass
class ScopedTeardown:
    """Teardown plan scoped to one disposable project."""

    project_name: str
    resources: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)


def plan_scoped_teardown(
    project_name: str, candidate_resources: list[str]
) -> ScopedTeardown:
    """Keep only project-scoped resources; refuse everything else.

    Never plans global prune (``docker volume prune``, ``down -v`` on the
    deployment project, unrelated operator volumes). Candidates that do not
    carry the project prefix are refused, not removed.
    """
    validate_disposable_project(project_name)
    plan = ScopedTeardown(project_name=project_name)
    prefix = f"{project_name}_"
    for candidate in candidate_resources:
        lowered = candidate.strip().lower()
        if lowered in {"docker volume prune", "docker system prune", "down -v"}:
            plan.refused.append(candidate)
            continue
        if candidate == DEPLOYMENT_PROJECT_NAME or candidate.startswith(
            f"{DEPLOYMENT_PROJECT_NAME}_"
        ):
            plan.refused.append(candidate)
            continue
        if candidate == project_name or candidate.startswith(prefix):
            plan.resources.append(candidate)
        else:
            plan.refused.append(candidate)
    return plan


def redact_diagnostics(text: str, max_chars: int = DIAGNOSTIC_MAX_CHARS) -> str:
    """Capture bounded diagnostics with secret-like values redacted."""
    redacted = text
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    if len(redacted) > max_chars:
        redacted = redacted[:max_chars] + f"\n…[truncated to {max_chars} chars]"
    return redacted


def build_timing_record(
    phase_seconds: dict[str, float],
    cache_condition: str,
    budget_seconds: float = TEN_MINUTE_BUDGET_SECONDS,
) -> dict[str, object]:
    """Build a per-phase timing record with cold/warm labeling.

    ``cache_condition`` must be ``"cold"`` or ``"warm"``; results are
    reported separately per condition. ``within_budget`` is a measured fact
    about this run, not a guarantee about other networks or providers.
    """
    if cache_condition not in ("cold", "warm"):
        raise ValueError(
            f"cache_condition must be 'cold' or 'warm', got {cache_condition!r}"
        )
    ordered = {phase: float(phase_seconds.get(phase, 0.0)) for phase in FIRST_RUN_PHASES}
    total = sum(ordered.values())
    return {
        "phases": ordered,
        "cache_condition": cache_condition,
        "total_seconds": total,
        "budget_seconds": budget_seconds,
        "within_budget": total <= budget_seconds,
        "budget_note": (
            "Measured budget under documented conditions; not a guarantee "
            "about arbitrary network speed or public-provider service."
        ),
    }


def build_live_report(
    *,
    revision: str,
    image_digests: dict[str, str],
    provider: str,
    model: str,
    cache_condition: str,
    timings: dict[str, object] | None = None,
    result: str = "unknown",
    cleanup_status: str = "unknown",
) -> dict[str, object]:
    """Schema for the protected live-qualification report (live tier only)."""
    if cache_condition not in ("cold", "warm"):
        raise ValueError(
            f"cache_condition must be 'cold' or 'warm', got {cache_condition!r}"
        )
    return {
        "revision": revision,
        "image_digests": dict(image_digests),
        "provider": provider,
        "model": model,
        "cache_condition": cache_condition,
        "phase_timings": timings or {},
        "result": result,
        "cleanup_status": cleanup_status,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
