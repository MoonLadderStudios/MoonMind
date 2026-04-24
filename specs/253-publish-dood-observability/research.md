# Research: Publish Durable DooD Observability Outputs

## Story Classification

Decision: Treat MM-504 as a single-story runtime feature request and a verification-first planning story.
Evidence: `spec.md` (Input); `specs/253-publish-dood-observability/spec.md`.
Rationale: The Jira brief defines one independently testable runtime outcome: durable artifact, report, and audit-metadata publication for Docker-backed workloads.
Alternatives considered: Broad design breakdown was rejected because the preserved Jira preset brief already selects one story and does not require processing multiple specs.
Test implications: Explicit unit and hermetic integration strategies are required, with verification-first escalation before production code changes.

## FR-001 / DESIGN-REQ-021 Minimum Durable Outputs

Decision: implemented_unverified.
Evidence: `moonmind/workloads/docker_launcher.py` publishes `runtime.stdout`, `runtime.stderr`, `runtime.diagnostics`, and declared output refs; `tests/unit/workloads/test_docker_workload_launcher.py` verifies successful publication, partial publication failure handling, and declared-output linkage; `tests/integration/temporal/test_profile_backed_workload_contract.py` exercises profile-backed artifact publication through the workload path.
Rationale: The current launcher already emits the core durable output set, but the story requires consistent proof across representative Docker-backed workload classes rather than only isolated launcher cases.
Alternatives considered: Mark as implemented_verified now. Rejected because the current evidence is strong for the launcher core but not yet explicit for every in-scope launch type named by MM-504.
Test implications: Focused unit verification first, plus hermetic integration if cross-class behavior needs proof or fixes.

## FR-002 Shared Report Publication Contract

Decision: partial.
Evidence: `moonmind/workloads/tool_bridge.py` maps declared report paths to `output.primary` and `output.summary`; `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/temporal/report_artifacts.py` define shared artifact and report-publication semantics; `tests/unit/workloads/test_workload_tool_bridge.py` validates report-path handling for curated workload tools.
Rationale: The repo has the structural pieces for shared report publication, but MM-504 specifically needs proof that Docker-backed workload outputs participate in the shared publication contract in the same way across the supported launch types.
Alternatives considered: Treat as implemented_unverified. Rejected because the current evidence is more indirect than FR-001 and still needs story-specific publication verification.
Test implications: Add unit and integration verification for report publication semantics; implementation only if report linkage or publication metadata diverges.

## FR-003 Authoritative Operator Inspection Record

Decision: implemented_unverified.
Evidence: `moonmind/workloads/docker_launcher.py` publishes bounded metadata alongside refs; `tests/unit/api/routers/test_task_runs.py` covers artifact-linked execution inspection; `tests/integration/temporal/test_temporal_artifact_lifecycle.py` exercises artifact lifecycle behavior.
Rationale: The runtime already prefers durable artifacts and metadata over transient process state, but MM-504 needs focused proof that operators can rely on those stored outputs for Docker-backed workloads without daemon-local evidence.
Alternatives considered: Mark as partial. Rejected because the existing artifact/read-model path appears aligned; the gap is verification depth rather than a clearly missing implementation.
Test implications: Focused unit verification with hermetic integration escalation if inspection surfaces drift.

## FR-004 / DESIGN-REQ-022 Bounded Audit Metadata

Decision: partial.
Evidence: `moonmind/workloads/docker_launcher.py` publishes workload metadata including `dockerHost`, `artifactPublication`, and runtime details; `moonmind/workloads/registry.py` and `moonmind/schemas/workload_models.py` define `workloadAccess` kinds; `tests/integration/temporal/test_integration_ci_tool_contract.py` and `tests/unit/workloads/test_workload_tool_bridge.py` cover some workload metadata paths.
Rationale: The repo already emits bounded workload metadata, but MM-504 needs stronger proof that unrestricted indicators, workload access class, and related audit fields are present and consistent across the supported workload modes and helper/run paths.
Alternatives considered: Treat as implemented_unverified. Rejected because the existing evidence is uneven across the full scope of audit metadata that the spec calls out.
Test implications: Add explicit unit and integration checks for metadata presence and normalized values.

## FR-005 / DESIGN-REQ-022 Redaction And Docker Host Normalization

Decision: partial.
Evidence: `moonmind/workloads/docker_launcher.py` redacts stdout, stderr, diagnostics, and top-level metadata through `redact_sensitive_text` and `redact_sensitive_payload`; the design doc requires normalized or redacted `dockerHost` and secret-like metadata values before publication.
Rationale: Redaction hooks exist in the launcher, but the current evidence set does not yet clearly prove the MM-504-specific guarantees for docker host normalization and secret-like value handling across all published workload outputs.
Alternatives considered: Mark as implemented_unverified. Rejected because the verification gap is large enough that a focused redaction check is still required before calling the requirement covered.
Test implications: Focused unit tests are required first; hermetic integration only if launcher changes become necessary.

## FR-006 Artifact-Class Consistency Across Launch Types

Decision: implemented_unverified.
Evidence: `moonmind/workflows/temporal/report_artifacts.py` and `moonmind/workflows/temporal/workflows/run.py` define canonical artifact class refs; `tests/unit/workflows/temporal/test_report_workflow_rollout.py` verifies report/generic link classification boundaries; `tests/unit/workloads/test_docker_workload_launcher.py` verifies runtime and output refs from launcher results.
Rationale: The runtime already appears to align on artifact-class semantics, but MM-504 requires direct proof that supported Docker-backed launch types publish results with consistent classes and observability expectations.
Alternatives considered: Mark as implemented_verified. Rejected because class-consistency proof is still distributed across different suites and not yet tied together as one story-level verification set.
Test implications: Focused unit verification plus hermetic integration escalation if cross-class drift appears.

## FR-007 Traceability

Decision: implemented_verified.
Evidence: `specs/253-publish-dood-observability/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/dood-observability-publication-contract.md` preserve MM-504 and the original Jira preset brief.
Rationale: The feature-local planning artifacts now preserve the Jira key and canonical brief required for downstream verification.
Alternatives considered: Treat as partial until tasks exist. Rejected because planning-stage traceability is fully satisfied by the current artifacts; later stages still need to preserve it, but planning is complete.
Test implications: Traceability review in later tasks and final verification.

## Repo Gap Analysis Outcome

Decision: No broad production-code change is justified at planning time; MM-504 should proceed verification-first.
Evidence: Existing launcher publication, report helper semantics, artifact API/read-model tests, and workload tool bridge coverage already implement most of the story’s runtime behavior.
Rationale: Planning should not invent implementation work when the repo already appears close to the desired contract. The remaining uncertainty is around explicit cross-path verification for audit metadata, redaction, and publication consistency.
Alternatives considered: Force code changes immediately to add more visible implementation scope. Rejected because that would bypass the evidence-first planning discipline required by MoonSpec.
Test implications: Tasks should start with focused unit verification, then full unit rerun, with `./tools/test_integration.sh` reserved for fixes that touch artifact publication, metadata serialization, or workload inspection boundaries.
