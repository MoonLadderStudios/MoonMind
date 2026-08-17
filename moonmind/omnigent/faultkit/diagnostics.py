"""Safe, credential-free diagnostic bundles for reproducing failures.

Owned by MoonLadderStudios/MoonMind#3709.

A diagnostic bundle contains everything needed to reproduce a failure without
credentials, network access, or raw production logs: the seed, the minimized
declarative scenario, the decision journal, the provider request log, the
observed vs. expected state, and the violated invariants. The bundle is scanned
for secret-like content before it is returned or written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from moonmind.omnigent.faultkit.invariants import InvariantViolation, check_invariants
from moonmind.omnigent.faultkit.recording import scan_for_secrets

if TYPE_CHECKING:  # pragma: no cover
    from moonmind.omnigent.faultkit.harness import RunResult


def build_diagnostic_bundle(
    result: "RunResult",
    *,
    violations: list[InvariantViolation] | None = None,
) -> dict[str, Any]:
    """Assemble a secret-free diagnostic bundle from a run result."""
    violations = violations if violations is not None else check_invariants(result)
    bundle: dict[str, Any] = {
        "schemaVersion": "moonmind.omnigent-fault-diagnostic/v1",
        "issue": "MoonLadderStudios/MoonMind#3709",
        "seed": result.scenario.seed,
        "scenarioName": result.scenario.name,
        "minimizedScenario": result.scenario.to_mapping(),
        "decisionJournal": result.decision_journal(),
        "providerRequestLog": result.recorder.to_journal(),
        "reconcilerView": result.reconciler_view.snapshot(),
        "referenceView": result.reference_view.snapshot(),
        "violations": [
            {"invariant": v.invariant, "detail": v.detail} for v in violations
        ],
    }
    leaks = scan_for_secrets(bundle)
    if leaks:
        raise ValueError(f"diagnostic bundle contains secret-like content: {leaks}")
    return bundle


def write_diagnostic_bundle(result: "RunResult", path: str | Path) -> Path:
    """Write a diagnostic bundle to ``path`` as JSON and return the path."""
    bundle = build_diagnostic_bundle(result)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    return out


__all__ = ["build_diagnostic_bundle", "write_diagnostic_bundle"]
