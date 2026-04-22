# Report Artifacts

Status: Draft  
Owners: MoonMind Platform + Mission Control  
Last updated: 2026-04-21

## 1. Purpose

This document defines how MoonMind should support workflows that produce a **report** as the final artifact or primary end-user deliverable.

Examples include:

- unit test reports
- coverage reports
- benchmark reports
- compliance reports
- security and penetration-testing reports
- investigation and incident-analysis reports
- agent-authored technical writeups assembled from execution evidence

This document is intentionally layered on top of the broader artifact system.
It does not redefine artifact storage internals, execution identity, workflow payload rules, or live observability APIs.

Instead, it defines:

- what a **report** means in MoonMind
- how reports should be modeled as artifact-backed outputs
- how reports should be linked to executions and steps
- how Mission Control and other consumers should present reports
- how report workflows should separate curated report content from raw evidence, logs, and diagnostics

---

## 1.1 Related docs and ownership boundaries

- `docs/Temporal/WorkflowArtifactSystemDesign.md`
  - Owns artifact identity, storage, linkage, retention, authorization, and lifecycle.
- `docs/Temporal/ArtifactPresentationContract.md`
  - Owns generic artifact presentation, preview behavior, metadata hints, and rendering rules.
- `docs/ManagedAgents/LiveLogs.md`
  - Owns observability, live tails, stdout/stderr, diagnostics, and session-aware run timelines.
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
  - Owns canonical runtime result contracts such as `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`.

This document owns the **report-specific artifact contract and presentation model**.

---

## 2. Scope / Non-goals

## 2.1 In scope

- report-specific artifact classes and `link_type` conventions
- recommended report bundle shape for workflow outputs
- report metadata needed for UI presentation and filtering
- rules for separating reports from evidence, diagnostics, and raw logs
- report retention and access expectations
- examples for unit-test and pentest-style workflows

## 2.2 Out of scope

- PDF rendering engines or document-conversion implementation details
- provider-specific report-generation prompts
- full-text search/indexing strategy for report bodies
- legal/compliance review procedures for report content
- replacing generic artifact APIs with an entirely separate report storage system

---

## 3. Core decision

MoonMind should **not** introduce a separate storage system for reports.

Instead:

1. **Reports are first-class artifact families.**
   - A report is stored in the existing artifact system.
   - Report workflows produce report-specific artifact link types and bounded metadata.

2. **A report is usually a bundle, not just one file.**
   - The human-facing report, machine-readable results, and supporting evidence should be stored as distinct linked artifacts.

3. **Curated report content is separate from observability.**
   - `stdout`, `stderr`, merged logs, diagnostics, session continuity artifacts, and provider snapshots remain observability/evidence surfaces, not the report itself.

4. **Large report payloads remain artifact-backed.**
   - Workflows and activities pass refs and compact summaries rather than embedding report bodies or evidence blobs in workflow history.

5. **Mission Control should treat reports as a first-class end state.**
   - If an execution has a canonical report artifact, the UI should surface it directly rather than forcing users to hunt through a generic artifact list.

---

## 4. Goals

1. **First-class final deliverables**  
   MoonMind should be able to represent “this workflow produced a report” as a clear, queryable end state.

2. **Bundle-friendly structure**  
   Report workflows should be able to publish a human-readable report, structured findings, and evidence without collapsing everything into one opaque blob.

3. **Artifact-first durability**  
   Reports must survive refreshes, reruns, worker restarts, and ended runs without depending on in-memory process state or workflow payload bloat.

4. **Safe presentation**  
   Sensitive reports should support preview-only or restricted raw access using the existing artifact presentation model.

5. **Workflow compatibility**  
   Unit-test, benchmark, pentest, compliance, and similar workflows should all fit one report contract without forcing one report schema onto all producers.

6. **Clear separation of concerns**  
   Reports, evidence, and observability should remain related but distinct surfaces.

---

## 5. Non-goals

- treating every `output.primary` artifact as a report
- storing evidence, logs, and diagnostics inline inside one giant report blob by default
- introducing mutable in-place report updates
- requiring every report workflow to produce PDF output
- making Mission Control parse provider-native raw payloads as if they were canonical reports

---

## 6. Definitions

## 6.1 Report artifact

A **Report artifact** is an immutable artifact whose primary purpose is to communicate the outcome of a workflow, step, or evaluation in a human-readable or machine-readable reporting form.

## 6.2 Report bundle

A **Report bundle** is the set of related artifacts that together form the report deliverable for a workflow or step.

A report bundle commonly includes:

- a primary human-facing report
- a short summary
- structured results or findings
- evidence attachments

## 6.3 Evidence artifact

An **Evidence artifact** is a durable supporting artifact linked to a report, such as:

- screenshots
- excerpts
- command results
- request/response captures
- reproducer output
- diff snippets
- benchmark measurements

## 6.4 Final report

A **Final report** is the canonical report deliverable for the completed execution or task.

## 6.5 Intermediate report

An **Intermediate report** is a report artifact created mid-workflow or at a step boundary for review, checkpointing, or iterative delivery.

---

## 7. Consumer and producer invariants

1. **Reports remain artifact-backed.**
   - The report body and large supporting evidence must live in artifacts, not workflow history.

2. **A report is not the same thing as logs or diagnostics.**
   - `runtime.stdout`, `runtime.stderr`, `runtime.merged_logs`, `runtime.diagnostics`, provider snapshots, and session continuity artifacts remain separate artifact classes.

3. **The human-facing report is the primary read target, not necessarily the only output.**
   - A workflow may publish multiple report-related artifacts, but one primary report should be treated as canonical for end-user presentation.

4. **Reports are immutable.**
   - Any revised report creates a new artifact ID.
   - “Latest report” is query behavior, not mutable state.

5. **UI clients must not guess which artifact is the report by local heuristics alone.**
   - Use report-specific `link_type`, bounded metadata, and server-defined latest behavior.

6. **Sensitive reports must degrade safely.**
   - If raw access is restricted, the UI should use preview/default-read behavior rather than assuming full download is allowed.

7. **Evidence should remain separately addressable.**
   - Screenshots, transcripts, and structured findings should not be irreversibly buried inside one rendered report file when separate durable access would improve auditability or reuse.

---

## 8. Recommended report artifact classes

MoonMind should add these stable `link_type` values for report-centric workflows.

## 8.1 Core report classes

- `report.primary`
  - Canonical human-facing report for the current scope.
- `report.summary`
  - Short executive summary or abstract.
- `report.structured`
  - Machine-readable findings or results.
- `report.evidence`
  - Supporting evidence attachment.

## 8.2 Optional report classes

- `report.appendix`
  - Extended detail that is intentionally separate from the main report.
- `report.findings_index`
  - Structured findings index optimized for UI grouping or filtering.
- `report.export`
  - Alternative rendered export such as HTML or PDF.

## 8.3 Relationship to existing output classes

Use these rules:

- Prefer `report.*` link types when the artifact is explicitly part of a report deliverable.
- Continue using `output.primary`, `output.summary`, and `output.agent_result` for generic non-report outputs.
- Keep observability and diagnostics in their existing runtime/debug classes.

This keeps report semantics explicit without breaking generic output flows.

---

## 9. Report bundle model

MoonMind should standardize on a compact workflow-facing result shape for report-producing flows.

Example:

```json
{
  "report_bundle_v": 1,
  "primary_report_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_..."
  },
  "summary_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_..."
  },
  "structured_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_..."
  },
  "evidence_refs": [
    {
      "artifact_ref_v": 1,
      "artifact_id": "art_..."
    }
  ],
  "report_type": "security_pentest_report",
  "report_scope": "final",
  "sensitivity": "restricted",
  "finding_counts": {
    "total": 8,
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 2
  }
}
```

Rules:

- This contract is compact and workflow-safe.
- The bundle contains refs and bounded metadata only.
- Large report bodies, finding details, screenshots, logs, and transcripts remain artifact-backed.

---

## 10. Metadata model for report artifacts

Report artifacts should continue using generic artifact metadata plus a small set of standardized report keys.

Recommended keys under `ArtifactMetadata.metadata`:

- `artifact_type`
  - Example: `unit_test_report`, `coverage_report`, `security_pentest_report`, `benchmark_report`
- `report_type`
  - Stable report family identifier
- `report_scope`
  - `final | intermediate | step | executive | technical`
- `title`
  - Human-facing display title
- `description`
  - Short display description
- `producer`
  - Workflow/tool/adapter identity such as `pytest`, `pentestgpt`, `benchmark-runner`
- `subject`
  - Bounded target description such as repo, test suite, hostname, challenge, or environment
- `render_hint`
  - `text | json | diff | image | binary`
- `name`
  - Suggested display filename/basename
- `is_final_report`
  - Boolean convenience flag when applicable
- `finding_counts`
  - Bounded counts only
- `severity_counts`
  - Bounded counts only
- `step_id`
  - Optional step-aware presentation hint
- `attempt`
  - Optional step attempt hint

Rules:

- Metadata must remain bounded and safe for control-plane display.
- Metadata must not contain secrets, raw access grants, cookies, session tokens, or large inline payloads.
- Detailed findings belong in `report.structured` or other linked artifacts, not in bloated metadata.

---

## 11. Storage and linkage rules

## 11.1 Storage

Report artifacts use the existing artifact store and index.

That means:

- immutable artifact IDs
- ordinary artifact lifecycle management
- execution linkage through the existing artifact link model
- ordinary preview and raw-download behavior

No separate report blob store is required.

## 11.2 Execution linkage

A report artifact should normally be linked to the producing execution using the standard execution reference:

- `namespace`
- `workflow_id`
- `run_id`
- `link_type`
- optional `label`

Examples:

- `link_type=report.primary`, `label="Final report"`
- `link_type=report.summary`, `label="Executive summary"`
- `link_type=report.structured`, `label="Findings JSON"`
- `link_type=report.evidence`, `label="Screenshot evidence"`

## 11.3 Step-aware linkage

When reports are step-scoped or iterative, producers should also include bounded step metadata such as:

- `step_id`
- `attempt`
- optional `scope=step`

This allows the UI to show both per-step reports and the final report without local guesswork.

## 11.4 “Latest report” semantics

Latest-report selection is a server query behavior, not mutable state.

Rules:

- For one execution, latest report should be resolved by `(namespace, workflow_id, run_id, link_type)`.
- For one task or multi-step flow, the UI should prefer an explicit final-report projection when available.
- Clients must not sort arbitrary artifacts in the browser and infer a canonical report.

---

## 12. Presentation rules

## 12.1 Primary UI surfaces

Report-producing executions should expose:

- a **Report** panel or top-level report card for the canonical final report
- a **Related Evidence** section for supporting artifacts
- continued access to **Artifacts**, **Stdout**, **Stderr**, **Diagnostics**, and other observability surfaces

The report surface should complement, not replace, the generic artifact surface.

## 12.2 Default read behavior

For a primary report:

- use the artifact system's `default_read_ref` for the default render target
- use `render_hint`, `content_type`, and `metadata.name/title` to choose the appropriate viewer
- expose raw download only when allowed

## 12.3 Recommended renderer behavior

Typical report renderers:

- `text/markdown` → text/markdown viewer
- `application/json` → JSON viewer for structured findings/results
- `text/plain` → text viewer
- `text/x-diff` → diff viewer for patch-like reports
- `image/*` → image viewer for screenshot evidence
- `application/pdf` or unknown binary → metadata + download-only unless a later PDF viewer is added deliberately

## 12.4 Report-first UX rule

If an execution has a canonical `report.primary` artifact, Mission Control should present that artifact before making the user inspect the generic artifact list.

Recommended behavior:

1. Query latest `report.primary` for the current execution.
2. If present, show a report card/panel with summary metadata and open action.
3. Show linked `report.summary`, `report.structured`, and `report.evidence` artifacts as related report content.
4. Fall back to the normal artifact list when no report artifact exists.

## 12.5 Evidence presentation

Evidence artifacts should remain individually addressable and viewable.

Examples:

- screenshots shown inline where safe
- JSON findings shown in the JSON viewer
- large logs or dumps kept as download/open surfaces rather than eagerly inlined into the report panel

---

## 13. Relationship to observability and diagnostics

This separation is mandatory.

## 13.1 Report vs observability

A report is the curated outcome surface.
Observability remains the operational truth surface.

Observability artifacts still include:

- `runtime.stdout`
- `runtime.stderr`
- `runtime.merged_logs`
- `runtime.diagnostics`
- provider snapshots
- session continuity artifacts
- debug traces

These artifacts may support or explain the report, but they are not themselves the report.

## 13.2 UI rule

Mission Control should not collapse raw logs and diagnostics into the report view by default.

Instead:

- the report panel shows curated deliverables
- the observability panels show how the run behaved
- the evidence surfaces show supporting proof

This is especially important for security and investigation workflows, where the final report, the raw transcript, and the execution diagnostics all matter but serve different purposes.

---

## 14. Security and access model

Report artifacts should use the existing artifact authorization model.

## 14.1 Sensitivity expectations

Reports may contain:

- sensitive internal code or architecture details
- security findings
- credentials accidentally captured in evidence
- PII or customer data
- provider operational payloads

## 14.2 Recommended redaction posture

Use these patterns:

- `report.primary` may be raw-readable for authorized users only
- `report.summary` may be less sensitive and broadly presentable
- `report.structured` may be preview-only or restricted depending on content
- `report.evidence` should often be treated as sensitive by default for security workflows

## 14.3 Preview support

When a report or evidence artifact is sensitive:

1. store the raw artifact
2. optionally generate a preview artifact
3. use `default_read_ref` to point clients at the preview when raw access is not allowed

This lets MoonMind show a useful report surface without widening raw-access permissions.

---

## 15. Retention guidance

Reports are usually more valuable than transient logs.

## 15.1 Recommended default mappings

- `report.primary` → `long`
- `report.summary` → `long`
- `report.structured` → `long` or `standard` depending on product policy
- `report.evidence` → `standard` or `long` depending on audit/review needs
- related raw logs and diagnostics keep their normal observability retention rules

## 15.2 Pinning

Final reports should be easy to pin from the UI.

Recommended behavior:

- allow explicit pin/unpin through the existing artifact API
- consider product-level auto-pinning for workflows where the final report is the primary deliverable

## 15.3 Deletion

Soft-delete/hard-delete behavior should remain artifact-system-native.

Report deletion must not implicitly delete unrelated observability artifacts unless a later product policy explicitly defines cascading behavior.

---

## 16. Workflow integration guidance

## 16.1 Workflow rule

Workflows should publish reports through activities that create and finalize artifacts, then return compact refs and bounded metadata.

Workflows must not:

- embed large report bodies in history
- embed screenshots or evidence blobs directly in workflow state
- embed raw download URLs in workflow payloads

## 16.2 Activity rule

Activities should be responsible for:

- assembling the report content
- writing `report.primary`, `report.summary`, `report.structured`, and `report.evidence` artifacts as needed
- linking them to the producing execution and optional step metadata
- returning a compact report bundle to workflow code

## 16.3 Finalization rule

When a workflow completes and the report is the primary deliverable, the producing path should ensure that one artifact is clearly marked and linked as the canonical final report.

Preferred mechanisms:

- `link_type=report.primary`
- `metadata.is_final_report=true`
- `metadata.report_scope=final`

This lets UI clients and later API projections identify the report without brittle heuristics.

---

## 17. Example workflow mappings

## 17.1 Unit test report

A unit-test workflow might publish:

- `report.primary`
  - Markdown or HTML summary of pass/fail totals, key failures, duration, and environment
- `report.summary`
  - Short executive test outcome summary
- `report.structured`
  - JUnit XML converted to JSON or a machine-readable results artifact
- `report.evidence`
  - Coverage summary image or selected failure excerpts when useful
- `runtime.stdout` / `runtime.stderr`
  - Raw test runner output
- `runtime.diagnostics`
  - Execution metadata, exit code, worker/runtime classification

Recommended metadata:

- `artifact_type=unit_test_report`
- `producer=pytest`
- `subject=<repo or suite name>`
- `finding_counts={ total, passed, failed, skipped }`

## 17.2 Coverage report

A coverage workflow might publish:

- `report.primary`
  - Human-readable coverage summary
- `report.structured`
  - Raw coverage JSON summary
- `report.evidence`
  - HTML coverage export or image snippets

Recommended metadata:

- `artifact_type=coverage_report`
- `subject=<repo/module>`
- `finding_counts={ files_below_threshold }`

## 17.3 Pentest or security report

A pentest-style workflow might publish:

- `report.primary`
  - Executive pentest report for the target
- `report.summary`
  - Short findings overview
- `report.structured`
  - Machine-readable findings JSON
- `report.evidence`
  - Screenshots, request/response excerpts, command outputs, proof-of-exploit captures
- `runtime.stdout` / `runtime.stderr`
  - Raw tool/runtime output
- `runtime.diagnostics`
  - Run diagnostics, tool failures, and environment details

Recommended metadata:

- `artifact_type=security_pentest_report`
- `producer=pentestgpt` or equivalent adapter name
- `subject=<target/engagement>`
- `severity_counts={ critical, high, medium, low, info }`
- `sensitivity=restricted`

## 17.4 Benchmark or evaluation report

A benchmark workflow might publish:

- `report.primary`
  - Human-readable benchmark summary
- `report.structured`
  - Per-case results JSON
- `report.evidence`
  - Charts, tables, or selected case output excerpts

Recommended metadata:

- `artifact_type=benchmark_report`
- `subject=<benchmark suite>`
- `finding_counts={ total_cases, passed_cases, failed_cases }`

---

## 18. Suggested API/UI extensions

The existing artifact APIs are sufficient for a first version, but MoonMind should consider adding report-aware convenience surfaces.

## 18.1 Execution detail summary fields

Recommended execution detail conveniences:

- `has_report`
- `latest_report_ref`
- `latest_report_summary_ref`
- `report_type`
- `report_status`
- bounded `finding_counts` / `severity_counts`

## 18.2 Report projection endpoint (optional future)

Possible future read model:

`GET /api/executions/{namespace}/{workflow_id}/{run_id}/report`

Example response:

```json
{
  "execution_ref": {
    "namespace": "moonmind",
    "workflow_id": "wf_123",
    "run_id": "run_456"
  },
  "report_type": "security_pentest_report",
  "primary_report_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_primary"
  },
  "summary_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_summary"
  },
  "structured_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_structured"
  },
  "evidence_refs": [
    {
      "artifact_ref_v": 1,
      "artifact_id": "art_evidence_1"
    }
  ],
  "finding_counts": {
    "total": 8
  }
}
```

Rules:

- This is a convenience projection over normal artifacts, not a second storage model.
- All underlying report artifacts still remain individually addressable through the standard artifact APIs.

---

## 19. Migration guidance

MoonMind does not need a flag-day migration.

Recommended rollout:

1. **Phase 1**  
   Recognize report intent through metadata conventions and existing artifact APIs.

2. **Phase 2**  
   Add explicit `report.*` link types and UI report surfacing.

3. **Phase 3**  
   Standardize a compact report bundle result contract for report-producing workflows.

4. **Phase 4**  
   Add report-aware projections, filters, retention defaults, and pinning affordances where useful.

During migration:

- existing generic outputs can continue using `output.primary`
- new report workflows should prefer explicit `report.*` semantics
- UI should degrade gracefully when only generic output artifacts exist

---

## 20. Open questions

1. Should MoonMind standardize a bounded enum for `report_type`, or keep it producer-defined with conventions first?
2. Should final reports be auto-pinned by default for some workflow families?
3. Do we want a dedicated report projection endpoint immediately, or should the first version rely entirely on standard artifact list queries?
4. Should `report.export` explicitly distinguish PDF/HTML exports from the editable or source-format report artifact?
5. Should report evidence support stronger grouping semantics such as `finding_id` or `section_id` in bounded metadata?
6. Should multi-step tasks expose both per-step reports and one task-level final report projection in Mission Control?

---

## 21. Bottom line

MoonMind should support report-producing workflows by making **reports a first-class artifact family on top of the existing artifact system**, not by inventing a separate report storage model.

That means:

- report outputs stay immutable and artifact-backed
- reports, evidence, and observability remain clearly separated
- workflows return compact report bundle refs rather than large payloads
- Mission Control can present a canonical final report directly when one exists
- sensitive reports can reuse the existing preview/restriction model

This gives MoonMind a clean way to support workflows such as unit-test reports, coverage reports, benchmark reports, and pentest reports while staying aligned with the current artifact-first Temporal architecture.
