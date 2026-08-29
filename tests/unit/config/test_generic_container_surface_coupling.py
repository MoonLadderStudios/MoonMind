"""The generic Container Jobs surfaces must name no consuming project.

MoonMind orchestrates provider- and project-maintained capabilities through
portable interfaces; a project's image, volume, in-container path, scenario
name, or gate vocabulary belongs in that project's Skill or in deployment
configuration, never in MoonMind's own request translation, resource
realization, lifecycle, or public tool contract.

This mirrors the scan a consuming project runs against the *deployed* MoonMind
package, so the coupling is refused at MoonMind's own required CI instead of
being discovered later against an already-published revision.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MOONMIND_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "moonmind"

#: The generic implementation surfaces whose request translation, resource
#: realization, lifecycle, and public tool contract must stay project-neutral.
GENERIC_CONTAINER_SURFACES: tuple[str, ...] = (
    "config/container_backend_settings.py",
    "container_job_cli.py",
    "mcp/container_job_tool_registry.py",
    "schemas/container_job_models.py",
    "security/container_job_capabilities.py",
    "workflows/temporal/container_job_backend.py",
    "workflows/temporal/workflows/container_job.py",
    "schemas/workload_models.py",
    "workloads/__init__.py",
    "workloads/docker_launcher.py",
    "workloads/gpu.py",
)

#: Consuming-project vocabulary that must never appear on a generic surface.
#: Each entry names the class of coupling so a violation reports what leaked.
PROHIBITED_PROJECT_COUPLING: dict[str, re.Pattern[str]] = {
    "project_repository": re.compile(
        r"MoonLadderStudios/Tactics|\bTactics\b|TACTICS_[A-Z0-9_]+", re.IGNORECASE
    ),
    "project_image_or_command": re.compile(
        r"tactics-(?:ue-render|visual-proof|render-doctor)", re.IGNORECASE
    ),
    "project_scenario": re.compile(
        r"presentation\.character\.melee_sequence|combat\.move_then_melee",
        re.IGNORECASE,
    ),
    "project_gate_or_bundle": re.compile(
        r"native[-_ ]gate|bundleVerified|observation[-_ ]bundle", re.IGNORECASE
    ),
    "engine_policy": re.compile(
        r"MOONMIND_UNREAL|\bUnreal(?: Engine)?\b|unreal_(?:ccache|ubt)", re.IGNORECASE
    ),
    "engine_arguments": re.compile(
        r"-nullrhi|-vulkan|-renderoffscreen", re.IGNORECASE
    ),
}


@pytest.mark.parametrize("relative", GENERIC_CONTAINER_SURFACES)
def test_generic_container_surface_names_no_project(relative: str) -> None:
    path = MOONMIND_PACKAGE_ROOT / relative
    # A missing surface fails closed: a renamed module must be re-listed here
    # rather than silently dropping out of the scan.
    assert path.is_file(), f"generic container surface is missing: {relative}"

    violations = [
        f"{relative}:{number} [{coupling}] {line.strip()[:160]}"
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        )
        for coupling, pattern in PROHIBITED_PROJECT_COUPLING.items()
        if pattern.search(line)
    ]
    assert not violations, "\n".join(
        ["generic container surfaces must name no consuming project:", *violations]
    )
