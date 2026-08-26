"""Static genericity guard for the GPU container path.

MoonLadderStudios/MoonMind#3777 requires the GPU container implementation and
its qualification fixtures to stay application-neutral. This guard fails if a
production GPU-container module, or a qualification fixture, acquires a game
name, an Unreal Engine filename/argument/cache name, a project image constant,
project gate/scenario/proof/bundle parsing, or any repository-specific
condition.

A consumer repository may cite this qualification externally; it must never add
its own semantics to this suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The canonical generic GPU module. Scanned in full.
GPU_MODULE = Path("moonmind/workloads/gpu.py")

#: Production modules that carry part of the GPU container path. Only their
#: GPU-bearing lines are in scope; the rest of each module predates and is
#: independent of this qualification.
GPU_BEARING_MODULES: tuple[Path, ...] = (
    Path("moonmind/schemas/workload_models.py"),
    Path("moonmind/schemas/container_job_models.py"),
    Path("moonmind/config/container_backend_settings.py"),
    Path("moonmind/workflows/temporal/container_job_backend.py"),
    Path("moonmind/workloads/docker_launcher.py"),
    Path("moonmind/workloads/registry.py"),
    Path("moonmind/workloads/tool_bridge.py"),
    Path("moonmind/workloads/__init__.py"),
)

#: Qualification fixtures for this issue. Scanned in full.
QUALIFICATION_FIXTURES: tuple[Path, ...] = (
    Path("tests/unit/workloads/test_gpu_container_qualification.py"),
    Path("tests/unit/workflows/temporal/test_gpu_container_dispatch.py"),
    Path("tests/integration/workloads/test_nvidia_container_qualification_journey.py"),
)

# Game and engine names, engine filenames, engine arguments, and engine cache
# names. Matched case-insensitively on word boundaries where a bare substring
# would produce false positives.
_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("game or project name", r"\btactics\b"),
    ("game or project name", r"\bunreal\b"),
    ("game or project name", r"\bue[45]\b"),
    ("engine project filename", r"\.uproject\b"),
    ("engine project filename", r"\.(umap|uasset)\b"),
    ("engine-specific argument", r"-(ExecCmds|NoSplash|Unattended|RenderOffScreen)\b"),
    ("engine-specific argument", r"\bAutomation\s+RunTest\b"),
    ("engine cache name", r"\b(ddc|ubt|ccache)[_-]?(volume|cache|dir)?\b"),
    ("project gate/scenario parsing", r"\b(scenario|gate|proof|bundle)s?\b"),
    ("repository-specific condition", r"\bmoonladderstudios/(?!moonmind\b)"),
)

# Image-like literals must never appear in production GPU code: the caller
# supplies the image as ordinary request data.
_IMAGE_LITERAL_PATTERN = re.compile(
    r"""["'][a-z0-9][a-z0-9._/-]*(?:nvidia|cuda)[a-z0-9._/-]*:[^"']+["']""",
    re.IGNORECASE,
)
_REGISTRY_LITERAL_PATTERN = re.compile(
    r"""["'](?:docker\.io|ghcr\.io|quay\.io|nvcr\.io)/[^"']+["']""",
    re.IGNORECASE,
)


def _read(relative: Path) -> str:
    path = REPO_ROOT / relative
    assert path.is_file(), f"guarded path is missing: {relative}"
    return path.read_text(encoding="utf-8")


def _violations(text: str, *, source: Path) -> list[str]:
    findings: list[str] = []
    for label, pattern in _FORBIDDEN_PATTERNS:
        for line_number, line in enumerate(text.splitlines(), start=1):
            if re.search(pattern, line, re.IGNORECASE):
                findings.append(f"{source}:{line_number} {label}: {line.strip()}")
    return findings


def _gpu_bearing_text(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if "gpu" in line.lower())


def test_gpu_module_contains_no_project_specific_semantics() -> None:
    assert _violations(_read(GPU_MODULE), source=GPU_MODULE) == []


@pytest.mark.parametrize("relative", GPU_BEARING_MODULES, ids=str)
def test_gpu_bearing_production_lines_contain_no_project_specific_semantics(
    relative: Path,
) -> None:
    assert _violations(_gpu_bearing_text(_read(relative)), source=relative) == []


@pytest.mark.parametrize("relative", QUALIFICATION_FIXTURES, ids=str)
def test_qualification_fixtures_contain_no_project_specific_semantics(
    relative: Path,
) -> None:
    assert _violations(_read(relative), source=relative) == []


def test_production_gpu_code_hardcodes_no_container_image() -> None:
    text = _read(GPU_MODULE)

    assert _IMAGE_LITERAL_PATTERN.search(text) is None
    assert _REGISTRY_LITERAL_PATTERN.search(text) is None


def test_qualification_image_and_command_are_fixture_data_only() -> None:
    from tests.unit.workloads.test_gpu_container_qualification import (
        QUALIFICATION_COMMAND,
        QUALIFICATION_IMAGE,
    )

    sources = [
        path
        for path in (REPO_ROOT / "moonmind").rglob("*.py")
        if path.is_file()
    ]
    sources.extend(
        path for path in (REPO_ROOT / "config").rglob("*.y*ml") if path.is_file()
    )
    # Short argv tokens such as ``sh`` and ``-lc`` are ordinary shell vocabulary;
    # only the distinctive fixture values prove the image and workload payload
    # never leaked into MoonMind as a product default.
    needles = (
        QUALIFICATION_IMAGE,
        *(part for part in QUALIFICATION_COMMAND if len(part) > 8),
    )
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {needle}"
        for path in sources
        for needle in needles
        if needle in path.read_text(encoding="utf-8", errors="ignore")
    ]

    assert offenders == []


def test_gpu_path_does_not_branch_on_workload_type() -> None:
    """MoonMind must not interpret what the caller's GPU workload does."""

    text = _read(GPU_MODULE).lower()

    for token in ("workload_type", "workloadtype", "if image", "image.startswith"):
        assert token not in text
