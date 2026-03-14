"""Align agentkit-orchestrate preset with publish-stage handoff strategy."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
import yaml
from alembic import op

revision: str = "202603010001"  # noqa: F401
down_revision: str | None = "202602260002"  # noqa: F401
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F401
depends_on: Union[str, Sequence[str], None] = None  # noqa: F401


_ALIGNMENT_TEMPLATE_YAML = """slug: agentkit-orchestrate
title: Spec Kit Orchestrate
description: Run the full Spec Kit pipeline from one feature request, including remediation loops, scope validation, and implementation/PR handoff.
scope: global
version: 1.0.0
tags:
  - agentkit
  - orchestration
  - runtime
  - docs
requiredCapabilities:
  - git
annotations:
  sourceSkill: agentkit-orchestrate
  profile: runtime-first
inputs:
  - name: feature_request
    label: Feature Request
    type: markdown
    required: true
  - name: orchestration_mode
    label: Orchestration Mode
    type: enum
    required: true
    default: runtime
    options:
      - runtime
      - docs
  - name: source_contract_path
    label: Source Contract Path
    type: text
    required: false
    default: ""
  - name: constraints
    label: Constraints
    type: textarea
    required: false
    default: ""
steps:
  - title: Intake feature request and determine mode
    instructions: |-
      Use this feature request as the canonical orchestration input:
      {{ inputs.feature_request }}

      Additional constraints:
      {{ inputs.constraints }}

      Selected mode: {{ inputs.orchestration_mode }}.
      Default to runtime mode and only switch to docs mode when explicitly requested.
      If the request is "Implement Docs/<path>.md", treat it as runtime intent and use the doc as a requirements contract.
      Source contract path (optional): {{ inputs.source_contract_path }}.
    skill:
      id: auto
      args: {}
  - title: Invoke agentkit-specify
    instructions: |-
      Run agentkit-specify with the canonical feature request.
      In runtime mode, append this scope guard:
      "Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."
      Preserve all user-provided constraints.
    skill:
      id: agentkit-specify
      args: {}
  - title: Resolve clarify blockers
    instructions: |-
      Resolve any clarification blockers from agentkit-specify before continuing.
      If blocking context is missing, ask targeted questions and update spec artifacts.
    skill:
      id: agentkit-clarify
      args: {}
  - title: Run DOC-REQ pre-plan gate when doc-backed
    instructions: |-
      If DOC-REQ-* identifiers are present in spec.md or the request is doc-backed:
      1. Ensure spec.md contains DOC-REQ-* IDs.
      2. Ensure every DOC-REQ-* maps to at least one functional requirement.
      If this fails, treat as CRITICAL and remediate before continuing.
    skill:
      id: auto
      args: {}
  - title: Invoke agentkit-plan
    instructions: |-
      Run agentkit-plan using the current feature artifacts.
      Keep runtime vs docs mode behavior aligned with the selected orchestration mode.
    skill:
      id: agentkit-plan
      args: {}
  - title: Enforce requirements traceability contract
    instructions: |-
      If DOC-REQ-* exists, require contracts/requirements-traceability.md with one row per DOC-REQ-* and non-empty validation strategy.
      Missing or incomplete traceability is CRITICAL and must be remediated.
    skill:
      id: auto
      args: {}
  - title: Invoke agentkit-tasks
    instructions: |-
      Run agentkit-tasks and generate dependency-ordered tasks.md from the planned artifacts.
    skill:
      id: agentkit-tasks
      args: {}
  - title: Validate task implementation scope
    instructions: |-
      Run:
      .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode {{ inputs.orchestration_mode }}

      In runtime mode, failure is CRITICAL:
      run remediation Prompt B, regenerate tasks, and re-run until pass or required context is missing.
    skill:
      id: auto
      args: {}
  - title: Validate DOC-REQ task coverage
    instructions: |-
      If DOC-REQ-* exists, verify tasks.md has at least one implementation task and one validation task for each DOC-REQ-*.
      Missing coverage is CRITICAL and must be fixed before continuing.
    skill:
      id: auto
      args: {}
  - title: Invoke agentkit-analyze
    instructions: |-
      Run agentkit-analyze against spec.md, plan.md, and tasks.md.
    skill:
      id: agentkit-analyze
      args: {}
  - title: Prompt A remediation discovery
    instructions: |-
      Run Prompt A (Remediation Discovery) over spec.md, plan.md, tasks.md, and latest agentkit-analyze output.
      Output must include:
      - Severity (CRITICAL/HIGH/MEDIUM/LOW)
      - Artifact
      - Location
      - Problem
      - Remediation
      - Rationale
      Plus:
      - Safe to Implement: YES | NO | NO DETERMINATION
      - Blocking Remediations
      - Determination Rationale

      In runtime mode classify "no production runtime code tasks" as CRITICAL.
      If DOC-REQ-* exists classify missing DOC-REQ-* mappings as CRITICAL.
    skill:
      id: auto
      args: {}
  - title: Prompt B remediation application
    instructions: |-
      Apply Prompt B (Remediation Application):
      - Complete all CRITICAL/HIGH remediations.
      - Complete MEDIUM/LOW unless conflicting with explicit constraints.
      - Keep edits deterministic across spec.md/plan.md/tasks.md.
      - For runtime mode, ensure production runtime code tasks and validation tasks exist.
      - If DOC-REQ-* exists, ensure implementation + validation coverage and traceability mappings.

      Report files changed, remediations completed/skipped, and residual risks.
    skill:
      id: auto
      args: {}
  - title: Re-run analysis and remediation loop gate
    instructions: |-
      Re-run agentkit-analyze once, then re-run Prompt A.
      Stop and report required context if Prompt A returns NO DETERMINATION.
      If Prompt A returns NO, run one extra best-effort cycle:
      Prompt B -> agentkit-analyze -> Prompt A.
      Continue unless result changes to NO.
    skill:
      id: agentkit-analyze
      args: {}
  - title: Invoke agentkit-implement with auto-proceed gate
    instructions: |-
      Run agentkit-implement with checklist gate mode set to auto-proceed.
      Do not pause for manual yes/no checklist confirmations in this orchestration.
      Ensure completed work is marked [X] in tasks.md.
    skill:
      id: agentkit-implement
      args:
        checklistGateMode: auto-proceed
      requiredCapabilities:
        - git
  - title: Validate completion coverage and implementation diff scope
    instructions: |-
      If DOC-REQ-* exists, verify each DOC-REQ-* appears in at least one completed ([X]) task.
      Then run:
      .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode {{ inputs.orchestration_mode }} --base-ref origin/main

      In runtime mode, if scope diff validation fails, do not commit or open a PR.
      Report "implementation scope not satisfied" and required corrective actions.
    skill:
      id: auto
      args: {}
      requiredCapabilities:
        - git
  - title: Return final report and defer publish actions
    instructions: |-
      If all gates pass (no NO DETERMINATION and no unresolved CRITICAL/HIGH blockers):
      1. Do NOT create commits, push branches, or open pull requests from runtime execution.
      2. Return a final report so MoonMind publish stage can handle commit/PR behavior when enabled.
      Return final report including:
      - Feature path and branch
      - Files edited
      - Test status
      - Safe-to-Implement determination
      - Checklist gate outcome
      - Scope validation outcomes (tasks + diff)
      - DOC-REQ coverage status
      - Publish handoff status (commit/PR handled by wrapper publish stage)
    skill:
      id: auto
      args: {}
"""


def _seed_file_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "data"
        / "task_step_templates"
        / "agentkit-orchestrate.yaml"
    )


def _load_seed_document() -> dict[str, object] | None:
    document = yaml.safe_load(_ALIGNMENT_TEMPLATE_YAML) or {}
    if not isinstance(document, dict):
        return None
    return document


def _template_uuid(slug: str, scope: str, scope_ref: str | None) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"task-template:{scope}:{scope_ref}:{slug}")


def _version_uuid(
    slug: str, scope: str, scope_ref: str | None, version: str
) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f"task-template-version:{scope}:{scope_ref}:{slug}:{version}",
    )


def upgrade() -> None:
    document = _load_seed_document()
    if document is None:
        return

    slug = str(document.get("slug") or "").strip()
    if not slug:
        return

    scope = str(document.get("scope") or "global").strip().lower() or "global"
    scope_ref = document.get("scopeRef")
    if scope_ref is not None:
        scope_ref = str(scope_ref).strip()
    version = str(document.get("version") or "1.0.0").strip() or "1.0.0"
    required_capabilities = list(document.get("requiredCapabilities") or [])
    steps = list(document.get("steps") or [])
    max_step_count = max(1, len(steps))
    seed_source = str(_seed_file_path())

    template_id = _template_uuid(slug=slug, scope=scope, scope_ref=scope_ref)
    version_id = _version_uuid(
        slug=slug,
        scope=scope,
        scope_ref=scope_ref,
        version=version,
    )

    bind = op.get_bind()
    templates_table = sa.table(
        "task_step_templates",
        sa.column("id", sa.Uuid()),
        sa.column("required_capabilities", sa.JSON()),
        sa.column("is_active", sa.Boolean()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    versions_table = sa.table(
        "task_step_template_versions",
        sa.column("id", sa.Uuid()),
        sa.column("required_capabilities", sa.JSON()),
        sa.column("steps", sa.JSON()),
        sa.column("max_step_count", sa.Integer()),
        sa.column("seed_source", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    bind.execute(
        sa.update(templates_table)
        .where(templates_table.c.id == template_id)
        .values(
            required_capabilities=required_capabilities,
            is_active=True,
            updated_at=sa.func.current_timestamp(),
        )
    )
    bind.execute(
        sa.update(versions_table)
        .where(versions_table.c.id == version_id)
        .values(
            required_capabilities=required_capabilities,
            steps=steps,
            max_step_count=max_step_count,
            seed_source=seed_source,
            updated_at=sa.func.current_timestamp(),
        )
    )


def downgrade() -> None:
    """No-op downgrade; this migration only refreshes seeded data content."""

    pass
