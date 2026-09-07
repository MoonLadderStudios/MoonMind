#!/usr/bin/env python3
"""Generate the mechanical Temporal workflow-type reference from production owners.

MoonLadderStudios/MoonMind#3959: the reference enumerates workflow classes and
Temporal type names from the production registration functions, Activity
names/fleets/routes from the Activity catalog and worker bindings, and Search
Attributes from their registration owner. It never hand-maintains a parallel
classification list.

Usage::

    python tools/generate_temporal_catalog.py --out docs/Temporal/WorkflowTypeCatalogGenerated.md
    python tools/generate_temporal_catalog.py --check --ref docs/Temporal/WorkflowTypeCatalogGenerated.md

``--check`` regenerates in memory and fails when the checked-in reference
drifts, so CI selects the temporal-boundary drift tests on any registration,
decorator/type-name, route, worker-construction, Search Attribute, or
generator/template change.

The command runs offline: no credentials, no network discovery, no live
Temporal, no container mutation. Import or metadata failures abort with an
actionable error; a partial catalog is never emitted as complete.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GENERATED_MARKER = "<!-- GENERATED FROM PRODUCTION REGISTRIES - DO NOT EDIT -->"
ISSUE_REF = "MoonLadderStudios/MoonMind#3959"

CANONICAL_DOC = Path("docs/Temporal/WorkflowTypeCatalogAndLifecycle.md")
OPERATOR_SURFACE_DOC = Path("docs/Temporal/SourceOfTruthAndProjectionModel.md")

#: Capability-routed skill aliases bound on every non-workflow fleet by
#: ``moonmind.workflows.temporal.activity_runtime.build_activity_bindings``
#: whenever skill activities are present. Mirrored here so the generated
#: reference enumerates the real per-fleet registrations; update with the
#: binding owner.
_SKILL_EXECUTION_ALIASES = ("mm.tool.execute", "mm.skill.execute")

#: Fleet name excluded from alias enumeration (workflow code hosts handlers,
#: it does not execute skill activities).
_WORKFLOW_FLEET_NAME = "workflow"

#: Workflow types with a human-authored lifecycle section in the canonical doc.
#: Types without an entry link to their generated anchor only; a missing
#: authored section must never drop a registered type from the reference.
LIFECYCLE_SECTIONS = {
    "MoonMind.UserWorkflow": "111-moonminduserworkflow-lifecycle",
    "MoonMind.ManifestIngest": "112-moonmindmanifestingest-lifecycle",
    "MoonMind.AgentRun": "113-moonmindagentrun-lifecycle",
    "MoonMind.OmnigentSession": "114-moonmindomnigentsession-lifecycle",
    "MoonMind.AgentSession": "115-moonmindagentsession-lifecycle",
    "MoonMind.ManagedSessionReconcile": "116-moonmindmanagedsessionreconcile-lifecycle",
    "MoonMind.ProviderProfileManager": "117-moonmindproviderprofilemanager-lifecycle",
    "MoonMind.OAuthSession": "118-moonmindoauthsession-lifecycle",
    "MoonMind.MergeAutomation": "119-moonmindmergeautomation-lifecycle",
}


@dataclass(frozen=True)
class WorkflowRow:
    temporal_type: str
    module: str
    class_name: str
    projection_scope: str


@dataclass(frozen=True)
class ActivityRow:
    activity_type: str
    fleet: str
    task_queue: str


def slugify_heading(heading: str) -> str:
    """Return the GitHub-style anchor slug for one Markdown heading."""

    slug = heading.strip().lower()
    slug = re.sub(r"`", "", slug)
    slug = re.sub(r"[^a-z0-9 _-]", "", slug)
    slug = slug.replace(" ", "-").replace("_", "-")
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug


def document_anchors(doc_path: Path) -> set[str]:
    """Return every heading anchor slug defined by one Markdown document."""

    anchors: set[str] = set()
    for line in doc_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"#{1,6}\s+(.*)", line)
        if match:
            anchors.add(slugify_heading(match.group(1)))
    return anchors


def generated_anchor(temporal_type: str) -> str:
    return slugify_heading(temporal_type)


def default_temporal_settings() -> object:
    """Return Temporal settings built from shipped defaults, ignoring ambient env.

    Explicit init values take precedence over ``TEMPORAL_*`` environment
    overrides in pydantic-settings, so the reference stays byte-stable no
    matter which deployment env invokes the generator.
    """

    from pydantic_core import PydanticUndefined

    from moonmind.config.settings import TemporalSettings

    overrides: dict[str, object] = {}
    for name, field in TemporalSettings.model_fields.items():
        if field.default is not PydanticUndefined:
            overrides[name] = field.default
        elif field.default_factory is not None:  # type: ignore[union-attr]
            overrides[name] = field.default_factory()  # type: ignore[operator]
    return TemporalSettings(**overrides)  # type: ignore[arg-type]


def clear_registry_caches() -> None:
    """Drop cached registry maps so tests that inject registrations stay visible."""

    from moonmind.workflows.temporal import workflow_registry
    from moonmind.workflows.temporal.hard_switch_cutover import (
        _resolve_renamed_user_workflow_start_contract,
    )

    for helper in (
        workflow_registry.workflow_fleet_workflow_classes,
        workflow_registry.workflow_fleet_activity_handlers,
        workflow_registry.workflow_projection_scopes,
        workflow_registry.checkpoint_branch_activity_handlers,
        _resolve_renamed_user_workflow_start_contract,
    ):
        cache_clear = getattr(helper, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()


def collect_workflows(
    registrations: Sequence[object] | None = None,
) -> tuple[WorkflowRow, ...]:
    """Enumerate validated workflow registrations in deterministic order."""

    from moonmind.workflows.temporal import workflow_registry

    clear_registry_caches()
    validated = workflow_registry.validate_workflow_registrations(
        None if registrations is None else tuple(registrations)
    )
    rows = tuple(
        WorkflowRow(
            temporal_type=temporal_name,
            module=registration.module,
            class_name=registration.class_name,
            projection_scope=registration.projection_scope,
        )
        for temporal_name, registration in validated.items()
    )
    return tuple(sorted(rows, key=lambda row: row.temporal_type))


def collect_activities() -> tuple[ActivityRow, ...]:
    """Enumerate Activity routes plus the real per-fleet skill aliases.

    The catalog owner defines one row per Activity definition; the worker
    binding owner
    (``moonmind.workflows.temporal.activity_runtime.build_activity_bindings``)
    additionally binds the ``mm.tool.execute`` / ``mm.skill.execute``
    capability-routed aliases on every non-workflow fleet's first task queue
    whenever skill activities are present (all production fleets receive
    them). Those alias rows are enumerated here from the production catalog
    fleets so the generated reference omits no real registration; the alias
    pair mirrors the binding owner and must be updated with it.
    """

    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )
    from moonmind.workflows.temporal.activity_runtime import (
        validate_activity_catalog_runtime_bindings,
    )

    catalog = build_default_activity_catalog(default_temporal_settings())  # type: ignore[arg-type]
    # Fail on unsupported route bindings before formatting anything.
    validate_activity_catalog_runtime_bindings(catalog)
    bound = {
        (definition.activity_type, definition.fleet)
        for definition in catalog.activities
    }
    alias_rows = tuple(
        sorted(
            (
                ActivityRow(
                    activity_type=alias,
                    fleet=fleet.fleet,
                    task_queue=fleet.task_queues[0],
                )
                for alias in _SKILL_EXECUTION_ALIASES
                for fleet in catalog.fleets
                if fleet.fleet != _WORKFLOW_FLEET_NAME
                and (alias, fleet.fleet) not in bound
            ),
            key=lambda row: (row.activity_type, row.fleet),
        )
    )
    rows = tuple(
        ActivityRow(
            activity_type=definition.activity_type,
            fleet=definition.fleet,
            task_queue=definition.task_queue,
        )
        for definition in catalog.activities
    ) + alias_rows
    return tuple(sorted(rows, key=lambda row: row.activity_type))


def collect_workflow_fleet_handlers() -> tuple[
    tuple[str, str], tuple[str, str]
]:
    """Split workflow-queue handlers into current vs historical-only lanes.

    Returns ``(current, historical)`` pairs of ``(activity_name, lane)``.
    Checkpoint-branch handlers remain only for pre-cutover histories recorded
    without a queue override (replay/in-flight compatibility); they must not
    be read as the live scheduling pattern.
    """

    from temporalio import activity

    from moonmind.workflows.temporal import workflow_registry

    clear_registry_caches()
    retained = set(
        workflow_registry.checkpoint_branch_activity_handlers()
    )
    current: list[tuple[str, str]] = []
    historical: list[tuple[str, str]] = []
    for handler in workflow_registry.workflow_fleet_activity_handlers():
        name = activity._Definition.must_from_callable(handler).name
        (historical if handler in retained else current).append(
            (name, "historical-only" if handler in retained else "current")
        )
    key = lambda item: item[0]  # noqa: E731
    return (tuple(sorted(current, key=key)), tuple(sorted(historical, key=key)))


def collect_search_attributes() -> tuple[tuple[str, str], tuple[str, str]]:
    """Return required and optional Search Attributes with their owner refs.

    The registration authority is
    ``services/temporal/scripts/bootstrap-namespace.sh``; owner refs below
    name the production code that writes each attribute. The lists must stay
    complete against that registry: a second partial list lets the reference
    stay green while real executions carry attributes it omits.
    """

    from moonmind.workflows.temporal.scheduled_start import (
        MM_SCHEDULED_FOR_SEARCH_ATTRIBUTE,
    )

    scheduled_for = str(MM_SCHEDULED_FOR_SEARCH_ATTRIBUTE or "").strip()
    if not scheduled_for:
        raise ValueError(
            "Search Attribute owner "
            "moonmind/workflows/temporal/scheduled_start.py declares an empty "
            "MM_SCHEDULED_FOR_SEARCH_ATTRIBUTE"
        )
    required = (
        ("mm_owner_id", "moonmind/workflows/temporal/service.py"),
        ("mm_owner_type", "moonmind/workflows/temporal/service.py"),
        ("mm_state", "moonmind/workflows/temporal/service.py"),
        ("mm_updated_at", "moonmind/workflows/temporal/service.py"),
        ("mm_entry", "moonmind/workflows/temporal/service.py"),
        (
            scheduled_for,
            "moonmind/workflows/temporal/scheduled_start.py",
        ),
        (
            "mm_started_at",
            "moonmind/workflows/temporal/workflows/run.py",
        ),
    )
    optional = (
        ("mm_repo", "moonmind/workflows/temporal/service.py"),
        ("mm_integration", "moonmind/workflows/temporal/service.py"),
        ("mm_target_runtime", "moonmind/workflows/temporal/service.py"),
        ("mm_target_skill", "moonmind/workflows/temporal/service.py"),
        ("mm_stage", "docs/Temporal/WorkflowTypeCatalogAndLifecycle.md §5.2"),
        ("mm_title", "moonmind/workflows/temporal/service.py"),
        (
            "mm_has_dependencies",
            "moonmind/workflows/temporal/workflows/run.py",
        ),
        (
            "mm_dependency_count",
            "moonmind/workflows/temporal/workflows/run.py",
        ),
        ("AgentRunId", "moonmind/workflows/temporal/workflows/run.py"),
        ("RuntimeId", "moonmind/workflows/temporal/workflows/run.py"),
        ("SessionId", "moonmind/workflows/temporal/workflows/run.py"),
        ("SessionEpoch", "moonmind/workflows/temporal/workflows/run.py"),
        ("SessionStatus", "moonmind/workflows/temporal/workflows/run.py"),
        (
            "IsDegraded",
            "moonmind/workflows/temporal/workflows/managed_runtime_workspace_cleanup.py",
        ),
    )
    return (required, optional)


def collect_workflow_queues() -> tuple[str, tuple[str, ...]]:
    """Resolve workflow-queue routing including the conditional replay queue."""

    from moonmind.workflows.temporal.activity_catalog import (
        get_workflow_poll_task_queues,
        get_workflow_task_queue,
    )

    cfg = default_temporal_settings()
    return (
        get_workflow_task_queue(cfg),  # type: ignore[arg-type]
        get_workflow_poll_task_queues(cfg),  # type: ignore[arg-type]
    )


def check_worker_agreement(rows: tuple[WorkflowRow, ...]) -> None:
    """Prove the reference agrees with actual worker construction (pinned SDK)."""

    from temporalio import workflow

    from moonmind.workflows.temporal import workflow_registry
    from moonmind.workflows.temporal.workers import (
        list_registered_workflow_types_for_settings,
    )

    clear_registry_caches()
    cfg = default_temporal_settings()
    generated = {row.temporal_type for row in rows}
    constructed = {
        workflow._Definition.must_from_class(cls).name
        for cls in workflow_registry.workflow_fleet_workflow_classes()
    }
    listed = set(list_registered_workflow_types_for_settings(cfg))  # type: ignore[arg-type]
    if generated != constructed or generated != listed:
        raise ValueError(
            "Generated catalog disagrees with actual worker construction: "
            f"generated={sorted(generated)} "
            f"worker_classes={sorted(constructed)} "
            f"worker_listing={sorted(listed)}"
        )


def check_lifecycle_links(rows: tuple[WorkflowRow, ...]) -> None:
    """Fail on dangling generated links into the canonical lifecycle doc."""

    anchors = document_anchors(REPO_ROOT / CANONICAL_DOC)
    dangling = [
        temporal_type
        for temporal_type in (row.temporal_type for row in rows)
        if temporal_type in LIFECYCLE_SECTIONS
        and LIFECYCLE_SECTIONS[temporal_type] not in anchors
    ]
    if dangling:
        raise ValueError(
            "Generated lifecycle links dangle against "
            f"{CANONICAL_DOC}: {', '.join(sorted(dangling))}"
        )


def render_reference(
    rows: tuple[WorkflowRow, ...],
    activities: tuple[ActivityRow, ...],
    current_handlers: tuple[tuple[str, str], ...],
    historical_handlers: tuple[tuple[str, str], ...],
    required_attributes: tuple[tuple[str, str], ...],
    optional_attributes: tuple[tuple[str, str], ...],
    start_queue: str,
    poll_queues: tuple[str, ...],
) -> str:
    """Render the deterministic mechanical reference (no timestamps/paths)."""

    rows = tuple(sorted(rows, key=lambda row: row.temporal_type))
    activities = tuple(sorted(activities, key=lambda item: item.activity_type))
    current_handlers = tuple(sorted(current_handlers, key=lambda item: item[0]))
    historical_handlers = tuple(sorted(historical_handlers, key=lambda item: item[0]))
    lines: list[str] = [
        GENERATED_MARKER,
        "# Temporal Workflow Type Reference (Generated)",
        "",
        f"Mechanical reference for {ISSUE_REF}, generated from the production",
        "registration owners. Do not edit by hand; regenerate with:",
        "",
        "```",
        "python tools/generate_temporal_catalog.py --out "
        + "docs/Temporal/WorkflowTypeCatalogGenerated.md",
        "```",
        "",
        "Providing owners:",
        "",
        "- Workflows: `moonmind/workflows/temporal/workflow_registry.py`",
        "  (`raw_workflow_registrations`, `validate_workflow_registrations`)",
        "- Activities/fleets/routes: `moonmind/workflows/temporal/activity_catalog.py`",
        "  (`build_default_activity_catalog`) with worker bindings in",
        "  `moonmind/workflows/temporal/activity_runtime.py`",
        "  (`validate_activity_catalog_runtime_bindings`)",
        "- Worker construction: `moonmind/workflows/temporal/worker_entrypoint.py`",
        "  and `moonmind/workflows/temporal/worker_runtime.py` via",
        "  `moonmind/workflows/temporal/workers.py`",
        "- Search Attributes: `moonmind/workflows/temporal/service.py`,",
        "  `moonmind/workflows/temporal/scheduled_start.py`,",
        "  `moonmind/workflows/temporal/workflows/run.py`, and",
        "  `moonmind/workflows/temporal/workflows/managed_runtime_workspace_cleanup.py`",
        "- Authored lifecycle semantics:",
        f"  `{CANONICAL_DOC.as_posix()}`",
        "",
        "Projection scope (`product` / `operator` / `excluded`) is the",
        "registry's visibility declaration only. It is not authorization,",
        "action capability, source readiness, or retirement status. Operator",
        "types link to the operator surface in",
        f"`{OPERATOR_SURFACE_DOC.as_posix()}`; valid controls per type live in",
        f"`{CANONICAL_DOC.as_posix()}` §6.2. Version-looking names (for example",
        "`MoonMind.PublicationRecoveryV1`) are first-class registrations and",
        "are never omitted. Conditional workflow-queue polling (start queue",
        "plus pre-patch replay queue) is declared by",
        "`get_workflow_poll_task_queues`; conditional registrations, when",
        "present, are listed with their supported condition below.",
        "",
        "## Workflow registrations",
        "",
        "| Temporal type | Module / class owner | Projection scope | Lifecycle |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        anchor = generated_anchor(row.temporal_type)
        lifecycle = f"#{anchor}"
        if row.temporal_type in LIFECYCLE_SECTIONS:
            # The generated reference lives beside the canonical doc, so the
            # link target is the sibling filename, not a repo-root path.
            lifecycle = (
                f"{CANONICAL_DOC.name}#{LIFECYCLE_SECTIONS[row.temporal_type]}"
            )
        lines.append(
            f"| `{row.temporal_type}` <a id=\"{anchor}\"></a> "
            f"| `{row.module}.{row.class_name}` "
            f"| `{row.projection_scope}` | [lifecycle]({lifecycle}) |"
        )
    lines += [
        "",
        "`MoonMind.ManifestIngest` retains two entry contracts as inputs to the",
        "single registered type above: the current catalogued-Activity path and",
        "the historical `manifest_read` / `manifest_compile` commands kept for",
        "replay (`moonmind/workflows/temporal/workflows/manifest_ingest.py`).",
        "They are not duplicate catalog entries.",
        "",
        "## Workflow task queues",
        "",
        f"New workflow starts use `{start_queue}`. The workflow fleet polls:",
        "",
    ]
    for queue in poll_queues:
        lines.append(f"- `{queue}`")
    lines += [
        "",
        "A pre-patch replay queue is polled only when it differs from the",
        "start queue (`get_workflow_poll_task_queues`); otherwise the fleet",
        "polls the start queue alone. This conditional queue is routing",
        "plumbing for in-flight histories, not product semantics.",
        "",
        "## Workflow-queue handler routing",
        "",
        "Current lane (new calls route here):",
        "",
    ]
    for name, _ in current_handlers:
        lines.append(f"- `{name}`")
    lines += ["", "Historical-only (pre-cutover histories, no new calls):", ""]
    for name, _ in historical_handlers:
        lines.append(f"- `{name}`")
    lines += [
        "",
        "The workflow-fleet helpers host regular Temporal activities colocated",
        "with deterministic workflow code, not Temporal Local Activities.",
        "",
        "## Activity catalog routes",
        "",
        "| Activity type | Fleet | Task queue |",
        "| --- | --- | --- |",
    ]
    for item in activities:
        lines.append(
            f"| `{item.activity_type}` | `{item.fleet}` | `{item.task_queue}` |"
        )
    lines += [
        "",
        "## Search Attributes",
        "",
        "Required:",
        "",
    ]
    for name, owner in required_attributes:
        lines.append(f"- `{name}` (owner: `{owner}`)")
    lines += ["", "Optional (only when product filtering requires them):", ""]
    for name, owner in optional_attributes:
        lines.append(f"- `{name}` (owner: `{owner}`)")
    lines += [
        "",
        "Runtime and primary-skill attributes must be registered before API",
        "filters or facets query them.",
        "",
    ]
    return "\n".join(lines) + "\n"


def generate() -> str:
    """Validate raw registrations before formatting and render the reference."""

    rows = collect_workflows()
    check_lifecycle_links(rows)
    check_worker_agreement(rows)
    activities = collect_activities()
    current_handlers, historical_handlers = collect_workflow_fleet_handlers()
    required_attributes, optional_attributes = collect_search_attributes()
    start_queue, poll_queues = collect_workflow_queues()
    return render_reference(
        rows,
        activities,
        current_handlers,
        historical_handlers,
        required_attributes,
        optional_attributes,
        start_queue,
        poll_queues,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_temporal_catalog",
        description="Generate the mechanical Temporal catalog from production owners.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the regenerated reference to this path.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in memory and fail when --ref drifts.",
    )
    parser.add_argument(
        "--ref",
        type=Path,
        default=None,
        help="Checked-in reference to compare against with --check.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        rendered = generate()
    except Exception as exc:
        # Never echo the raw exception: Pydantic settings validation errors
        # embed rejected input values, which may carry secret-bearing
        # settings (MoonLadderStudios/MoonMind#3959 review). Log the error
        # class only; rerunning the command reproduces the full message
        # locally without persisting secrets to CI logs.
        failure = f"temporal catalog generation failed: {type(exc).__name__}"
        print(f"{failure} (settings inputs withheld)", file=sys.stderr)
        return 1

    if args.check:
        if args.ref is None:
            print(
                "temporal catalog check requires --ref <checked-in reference>",
                file=sys.stderr,
            )
            return 2
        ref_path = REPO_ROOT / args.ref
        try:
            checked_in = ref_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"temporal catalog check failed: cannot read {args.ref}: {exc}",
                file=sys.stderr,
            )
            return 1
        if checked_in != rendered:
            print(
                f"temporal catalog drift: {args.ref} differs from production "
                "registries; regenerate with "
                "python tools/generate_temporal_catalog.py --out " + str(args.ref),
                file=sys.stderr,
            )
            return 1
        print(f"temporal catalog check passed: {args.ref} matches registries")
        return 0

    if args.out is None:
        sys.stdout.write(rendered)
        return 0
    out_path = REPO_ROOT / args.out
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(rendered, encoding="utf-8")
    tmp_path.replace(out_path)
    print(f"temporal catalog generated: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
