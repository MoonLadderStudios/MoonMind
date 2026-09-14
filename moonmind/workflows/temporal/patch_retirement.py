"""Read-only audit of Temporal ``workflow.patched()`` retirement candidates.

MoonLadderStudios/MoonMind#3944.

A workflow start-date query, zero currently OPEN runs, or two elapsed releases
is not enough evidence to delete an old patch branch and its marker. This
module combines the evidence the issue requires before any retirement:

* deployment versions and the admission cutoff for new work,
* relevant running executions,
* retained histories and recorded patch markers,
* continue-as-new chains where applicable,
* persisted old inputs,
* the operating reset/replay policy for retained closed executions.

Every decision distinguishes ``UNKNOWN`` (evidence incomplete) from ``no
consumers``. A failed Visibility or history query never reports zero
consumers.

The module is intentionally side-effect free: inventory is a static AST scan
of the checked-in workflow files, the audit is a pure function of explicit
evidence, and the CLI only prints. Nothing here starts, signals, resets, or
deletes a workflow execution or its history.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Mapping

WORKFLOW_DIRNAME = "moonmind/workflows/temporal/workflows"
WORKFLOW_FILE_SUFFIX = ".py"

# Retirement proceeds one stage at a time. ``deprecate`` keeps the call site
# as ``workflow.deprecate_patch()`` so pre-change histories still replay while
# new executions stop recording the marker. ``remove`` deletes the call site
# entirely and is only safe once no retained history can still carry the
# marker under the supported reset/replay policy.
STAGE_DEPRECATE = "deprecate"
STAGE_REMOVE = "remove"

VERDICT_SAFE_TO_DEPRECATE = "safe_to_deprecate"
VERDICT_SAFE_TO_REMOVE = "safe_to_remove"
VERDICT_REQUIRES_COMPATIBILITY = "requires_compatibility"
VERDICT_UNKNOWN = "unknown"

USAGE_BRANCH = "branch"
USAGE_BARE_MARKER = "bare_marker"
USAGE_DEPRECATED = "deprecated"


@dataclass(frozen=True)
class PatchRecord:
    """One statically discovered patch call site."""

    patch_id: str
    constant_name: str | None
    file: str
    line: int
    workflow_type: str
    usage_kind: str
    introduction_rev: str | None = None


@dataclass(frozen=True)
class AuditEvidence:
    """Operator-supplied evidence for one audit run.

    All fields default to the conservative pole: empty collections mean "not
    observed", while any query failure forces UNKNOWN rather than "no
    consumers". Callers must populate these from deployment state, Visibility,
    and history inspection; this module never queries Temporal itself so the
    audit stays read-only and hermetic.
    """

    deployment_versions: tuple[str, ...] = ()
    admission_cutoff: str | None = None
    pre_patch_workers_present: bool = False
    running_may_predate_patch: bool = False
    retained_markers: frozenset[str] = frozenset()
    continue_as_new_chains: frozenset[str] = frozenset()
    pending_old_inputs: frozenset[str] = frozenset()
    reset_replay_supported: bool = True
    visibility_failures: tuple[str, ...] = ()
    history_failures: tuple[str, ...] = ()
    stale_visibility: bool = False


@dataclass(frozen=True)
class PatchFinding:
    """Per-patch audit outcome for one retirement stage."""

    patch_id: str
    stage: str
    verdict: str
    reasons: tuple[str, ...]
    record: PatchRecord | None = None


@dataclass(frozen=True)
class AuditReport:
    """Bounded, read-only audit outcome."""

    findings: tuple[PatchFinding, ...]
    truncated: int = 0
    unknown_evidence: tuple[str, ...] = ()

    def summary_counts(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.verdict] = counts.get(finding.verdict, 0) + 1
        return counts


def _workflow_type_for_source(tree: ast.Module, line: int) -> str:
    """Return the innermost enclosing class/function name for a call site."""
    best = ""
    best_depth = -1

    def visit(node: ast.AST, depth: int, name: str) -> None:
        nonlocal best, best_depth
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                child_name = f"{name}.{child.name}" if name else child.name
                child_start = getattr(child, "lineno", 0)
                child_end = getattr(child, "end_lineno", 0)
                if child_start <= line <= child_end and depth > best_depth:
                    best = child_name
                    best_depth = depth
                visit(child, depth + 1, child_name)
            else:
                visit(child, depth, name)

    visit(tree, 0, "")
    return best or "<module>"


def _constant_patch_ids(tree: ast.Module) -> dict[str, str]:
    """Map module-level ``*_PATCH`` / ``*_PATCH_ID`` constants to string ids."""
    mapping: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or (
            not target.id.endswith("_PATCH") and not target.id.endswith("_PATCH_ID")
        ):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            mapping[target.id] = value.value
    return mapping


def _function_argument_names(tree: ast.Module) -> set[str]:
    """Collect every function/method argument name in the module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            ):
                names.add(arg.arg)
            if node.args.vararg is not None:
                names.add(node.args.vararg.arg)
            if node.args.kwarg is not None:
                names.add(node.args.kwarg.arg)
    return names


def _is_patch_call(func: ast.Attribute) -> bool:
    if not isinstance(func.value, ast.Name):
        return False
    if func.value.id == "workflow":
        return func.attr in ("patched", "deprecate_patch")
    # Project helper that routes patch markers through one guarded call site
    # (e.g. ``MoonMindAgentRun._workflow_patch_enabled``).
    return func.attr == "_workflow_patch_enabled"


def _patch_call_sites(
    tree: ast.Module,
) -> Iterable[tuple[str, int, str | None, bool]]:
    """Yield ``(call_name, line, constant_or_literal, bare)`` for patch calls.

    ``bare`` is true when the call is the entire expression statement (its
    return value is discarded and only the marker position is retained for
    history continuity); otherwise the return value drives a behavior branch.
    """
    found: list[tuple[str, int, str | None, bool]] = []

    def visit(node: ast.AST, is_expr_value: bool) -> None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _is_patch_call(node.func)
            and node.args
        ):
            first = node.args[0]
            lineno = getattr(node, "lineno", 0)
            if isinstance(first, ast.Name):
                found.append((node.func.attr, lineno, first.id, is_expr_value))
            elif isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append((node.func.attr, lineno, None, is_expr_value))
        for child in ast.iter_child_nodes(node):
            visit(child, isinstance(node, ast.Expr) and child is node.value)

    visit(tree, False)
    return found


def inventory_patches(repo_root: str | Path) -> list[PatchRecord]:
    """Statically inventory live patch call sites (read-only).

    Scans every ``*.py`` file under the Temporal workflows directory, resolves
    ``workflow.patched(CONSTANT)`` / ``workflow.patched("literal")`` /
    ``workflow.deprecate_patch(...)`` call sites, and classifies each as a
    behavior ``branch`` (return value consumed), a ``bare_marker`` (return
    value discarded, position retained for history continuity), or
    ``deprecated`` (already on the ``deprecate_patch`` bridge).
    """
    root = Path(repo_root)
    workflow_dir = root / WORKFLOW_DIRNAME
    records: list[PatchRecord] = []
    for path in sorted(workflow_dir.glob(f"*{WORKFLOW_FILE_SUFFIX}")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        constants = _constant_patch_ids(tree)
        arg_names = _function_argument_names(tree)
        source_lines = path.read_text(encoding="utf-8").splitlines()
        for call_name, line, reference, bare in _patch_call_sites(tree):
            patch_id: str | None = None
            constant_name: str | None = None
            if reference is not None and reference in constants:
                patch_id = constants[reference]
                constant_name = reference
            elif reference is not None and reference in arg_names:
                # Dynamic dispatch through a parameter (e.g. the guarded
                # helper's own ``workflow.patched(patch_id)``): the concrete
                # markers are inventoried at the helper's call sites, so
                # skipping here avoids a misleading pseudo-id.
                continue
            elif reference is not None:
                # A bare name that is neither a module-level string constant
                # nor a parameter (imported alias): record the reference as-is
                # so the inventory stays complete rather than silent.
                patch_id = reference
                constant_name = reference
            else:
                # Literal string id; recover it from the call site.
                patch_id = _literal_patch_id(source_lines, line)
                if patch_id is None:
                    continue
            if call_name == "deprecate_patch":
                usage_kind = USAGE_DEPRECATED
            elif bare:
                usage_kind = USAGE_BARE_MARKER
            else:
                usage_kind = USAGE_BRANCH
            records.append(
                PatchRecord(
                    patch_id=patch_id,
                    constant_name=constant_name,
                    file=f"{WORKFLOW_DIRNAME}/{path.name}",
                    line=line,
                    workflow_type=_workflow_type_for_source(tree, line),
                    usage_kind=usage_kind,
                )
            )
    records.sort(key=lambda record: (record.patch_id, record.file, record.line))
    return records


def _literal_patch_id(source_lines: list[str], line: int) -> str | None:
    if line < 1 or line > len(source_lines):
        return None
    text = source_lines[line - 1]
    try:
        tree = ast.parse(text.strip())
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return first.value
    return None


def annotate_introduction_revs(
    records: Iterable[PatchRecord],
    repo_root: str | Path,
    *,
    max_probes: int = 25,
) -> list[PatchRecord]:
    """Best-effort ``git log -S`` introduction revision per patch id.

    Bounded by ``max_probes`` git invocations; failures and unprobed records
    keep ``introduction_rev=None`` (unknown evidence, never "no history").
    Read-only: only ``git log`` is executed.
    """
    root = Path(repo_root)
    annotated: list[PatchRecord] = []
    probes = 0
    cache: dict[str, str | None] = {}
    for record in records:
        if record.patch_id in cache:
            annotated.append(replace(record, introduction_rev=cache[record.patch_id]))
            continue
        rev: str | None = None
        if probes < max_probes:
            probes += 1
            rev = _oldest_commit_touching(root, record.patch_id)
        cache[record.patch_id] = rev
        annotated.append(replace(record, introduction_rev=rev))
    return annotated


def _oldest_commit_touching(repo_root: Path, patch_id: str) -> str | None:
    try:
        completed = subprocess.run(
            [
                "git",
                "log",
                "--format=%H",
                "--reverse",
                "-S",
                patch_id,
                "--",
                WORKFLOW_DIRNAME,
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    for candidate in completed.stdout.splitlines():
        candidate = candidate.strip()
        if candidate:
            return candidate
    return None


def _evidence_problems(evidence: AuditEvidence) -> tuple[str, ...]:
    problems = list(evidence.visibility_failures) + list(evidence.history_failures)
    if evidence.stale_visibility:
        problems.append("stale_visibility")
    return tuple(problems)


def audit_patch(
    record: PatchRecord,
    evidence: AuditEvidence,
    *,
    stage: str = STAGE_DEPRECATE,
) -> PatchFinding:
    """Decide one patch's retirement readiness for one stage.

    A failed Visibility/history query or stale Visibility always yields
    UNKNOWN: the audit must not report zero consumers when the query itself
    failed. Mixed deployment versions, running executions that may predate the
    patch, retained markers, continue-as-new chains, and pending old inputs
    require continued compatibility. Marker removal additionally requires that
    no retained history can still carry the marker under the supported
    reset/replay policy.
    """
    if stage not in (STAGE_DEPRECATE, STAGE_REMOVE):
        raise ValueError(f"unsupported retirement stage: {stage!r}")
    problems = _evidence_problems(evidence)
    if problems:
        return PatchFinding(
            patch_id=record.patch_id,
            stage=stage,
            verdict=VERDICT_UNKNOWN,
            reasons=tuple(f"evidence incomplete: {problem}" for problem in problems),
            record=record,
        )
    blockers: list[str] = []
    if evidence.pre_patch_workers_present:
        blockers.append("mixed deployment versions include pre-patch workers")
    if evidence.running_may_predate_patch:
        blockers.append("running executions may predate the patch")
    if record.patch_id in evidence.retained_markers:
        blockers.append("retained histories carry the patch marker")
    if record.patch_id in evidence.continue_as_new_chains:
        blockers.append("continue-as-new chains carry pre-patch state")
    if record.patch_id in evidence.pending_old_inputs:
        blockers.append("persisted old inputs still reference the pre-patch shape")
    if blockers:
        return PatchFinding(
            patch_id=record.patch_id,
            stage=stage,
            verdict=VERDICT_REQUIRES_COMPATIBILITY,
            reasons=tuple(blockers),
            record=record,
        )
    if stage == STAGE_REMOVE and evidence.reset_replay_supported:
        # Closed histories remain within retention and the operating policy
        # supports resetting/replaying them, so an unobserved marker cannot be
        # proven absent. Removal stays blocked; deprecation is the bridge.
        return PatchFinding(
            patch_id=record.patch_id,
            stage=stage,
            verdict=VERDICT_REQUIRES_COMPATIBILITY,
            reasons=(
                "retained closed histories remain reset/replay eligible "
                "under the operating policy",
            ),
            record=record,
        )
    if stage == STAGE_DEPRECATE:
        verdict = VERDICT_SAFE_TO_DEPRECATE
    else:
        verdict = VERDICT_SAFE_TO_REMOVE
    return PatchFinding(
        patch_id=record.patch_id,
        stage=stage,
        verdict=verdict,
        reasons=("no consumers observed across healthy evidence sources",),
        record=record,
    )


def audit_all(
    records: Iterable[PatchRecord],
    evidence: AuditEvidence,
    *,
    stage: str = STAGE_DEPRECATE,
    max_entries: int = 200,
) -> AuditReport:
    """Audit every record for one stage; bound the report to ``max_entries``."""
    ordered = sorted(
        records, key=lambda record: (record.patch_id, record.file, record.line)
    )
    findings = [audit_patch(record, evidence, stage=stage) for record in ordered]
    truncated = max(0, len(findings) - max_entries)
    return AuditReport(
        findings=tuple(findings[:max_entries]),
        truncated=truncated,
        unknown_evidence=_evidence_problems(evidence),
    )


def retirement_gate(record: PatchRecord, evidence: AuditEvidence) -> tuple[str, str]:
    """Route admission for one patch: stop new dependencies before retirement.

    Returns ``(disposition, reason)`` where disposition is one of
    ``route_new_only`` (old workers/admissions must not create new
    dependencies on the pre-patch path), ``block_unknown`` (evidence failed;
    hold retirement and new old-path admissions), or ``open``.
    """
    problems = _evidence_problems(evidence)
    if problems:
        return (
            "block_unknown",
            f"hold retirement: evidence incomplete ({', '.join(problems)})",
        )
    if (
        evidence.pre_patch_workers_present
        or evidence.running_may_predate_patch
        or record.patch_id in evidence.retained_markers
        or record.patch_id in evidence.continue_as_new_chains
        or record.patch_id in evidence.pending_old_inputs
    ):
        return (
            "route_new_only",
            "old workers/admissions must not create new pre-patch dependencies",
        )
    return ("open", "no consumers observed across healthy evidence sources")


def report_to_json(report: AuditReport, evidence: AuditEvidence) -> dict:
    return {
        "stage": report.findings[0].stage if report.findings else STAGE_DEPRECATE,
        "summary": dict(report.summary_counts()),
        "truncated": report.truncated,
        "unknownEvidence": list(report.unknown_evidence),
        "deploymentVersions": list(evidence.deployment_versions),
        "admissionCutoff": evidence.admission_cutoff,
        "findings": [
            {
                "patchId": finding.patch_id,
                "verdict": finding.verdict,
                "reasons": list(finding.reasons),
                "file": finding.record.file if finding.record else None,
                "line": finding.record.line if finding.record else None,
                "usage": finding.record.usage_kind if finding.record else None,
            }
            for finding in report.findings
        ],
    }


def render_report_markdown(report: AuditReport, evidence: AuditEvidence) -> str:
    lines = [
        "# Workflow patch retirement audit",
        "",
        f"Deployment versions: {', '.join(evidence.deployment_versions) or 'unknown'}",
        f"Admission cutoff: {evidence.admission_cutoff or 'none recorded'}",
        f"Unknown evidence: {', '.join(report.unknown_evidence) or 'none'}",
        "",
        "## Summary",
        "",
    ]
    for verdict, count in sorted(report.summary_counts().items()):
        lines.append(f"- {verdict}: {count}")
    if report.truncated:
        lines.append(
            f"- truncated: {report.truncated} further findings omitted by bound"
        )
    lines += ["", "## Findings", ""]
    for finding in report.findings:
        location = ""
        if finding.record is not None:
            location = f" ({finding.record.file}:{finding.record.line})"
        lines.append(f"- `{finding.patch_id}`{location}: **{finding.verdict}**")
        for reason in finding.reasons:
            lines.append(f"  - {reason}")
    return "\n".join(lines) + "\n"


def evidence_from_mapping(payload: Mapping) -> AuditEvidence:
    """Build evidence from a plain mapping (e.g. a JSON evidence file).

    Absent query-health keys default to unknown failures: an evidence file
    that says nothing about Visibility/history health must never read as
    "no consumers". An operator asserts healthy queries explicitly with
    empty failure lists.
    """
    missing = object()
    visibility_failures = payload.get("visibilityFailures", missing)
    if visibility_failures is missing:
        visibility_failures = ("visibility state not supplied",)
    history_failures = payload.get("historyFailures", missing)
    if history_failures is missing:
        history_failures = ("retained-history state not supplied",)
    return AuditEvidence(
        deployment_versions=tuple(payload.get("deploymentVersions", ())),
        admission_cutoff=payload.get("admissionCutoff"),
        pre_patch_workers_present=bool(payload.get("prePatchWorkersPresent", False)),
        running_may_predate_patch=bool(payload.get("runningMayPredatePatch", False)),
        retained_markers=frozenset(payload.get("retainedMarkers", ())),
        continue_as_new_chains=frozenset(payload.get("continueAsNewChains", ())),
        pending_old_inputs=frozenset(payload.get("pendingOldInputs", ())),
        reset_replay_supported=bool(payload.get("resetReplaySupported", True)),
        visibility_failures=tuple(visibility_failures),
        history_failures=tuple(history_failures),
        stale_visibility=bool(payload.get("staleVisibility", False)),
    )


def build_audit(
    repo_root: str | Path,
    evidence: AuditEvidence,
    *,
    stage: str = STAGE_DEPRECATE,
    max_entries: int = 200,
    annotate_revs: bool = False,
    max_rev_probes: int = 25,
) -> tuple[AuditReport, AuditEvidence]:
    records = inventory_patches(repo_root)
    if annotate_revs:
        records = annotate_introduction_revs(
            records, repo_root, max_probes=max_rev_probes
        )
    return audit_all(records, evidence, stage=stage, max_entries=max_entries), evidence


def main(argv: list[str] | None = None) -> int:
    """Read-only CLI: ``python -m moonmind.workflows.temporal.patch_retirement``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=".",
        help="repository root containing moonmind/workflows/temporal/workflows",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="report rendering",
    )
    parser.add_argument(
        "--stage",
        choices=(STAGE_DEPRECATE, STAGE_REMOVE),
        default=STAGE_DEPRECATE,
        help="retirement stage under review",
    )
    parser.add_argument(
        "--max-entries",
        type=int,
        default=200,
        help="bound on reported findings (remaining count is disclosed)",
    )
    parser.add_argument(
        "--evidence-json",
        default=None,
        help="optional JSON file with AuditEvidence fields; absent means "
        "fully unknown evidence (every finding reports unknown)",
    )
    parser.add_argument(
        "--annotate-revs",
        action="store_true",
        help="opt-in bounded git log -S introduction-revision annotation",
    )
    args = parser.parse_args(argv)

    if args.evidence_json is not None:
        payload = json.loads(Path(args.evidence_json).read_text(encoding="utf-8"))
        evidence = evidence_from_mapping(payload)
    else:
        # No evidence supplied: every consumer question is unknown. This is
        # the fail-closed default, never a claim of zero consumers.
        evidence = AuditEvidence(
            visibility_failures=("no evidence supplied: Visibility state unknown",),
            history_failures=("no evidence supplied: retained-history state unknown",),
        )
    report, _ = build_audit(
        args.repo_root,
        evidence,
        stage=args.stage,
        max_entries=args.max_entries,
        annotate_revs=args.annotate_revs,
    )
    if args.format == "json":
        print(json.dumps(report_to_json(report, evidence), indent=2, sort_keys=True))
    else:
        print(render_report_markdown(report, evidence), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
