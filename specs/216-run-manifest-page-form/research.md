# Research: Run Manifest Page Form

## FR-001 / DESIGN-REQ-001

Decision: implemented_verified.
Evidence: `frontend/src/entrypoints/manifests.tsx` renders `Run Manifest` before `Recent Runs`; `frontend/src/entrypoints/manifests.test.tsx` verifies both headings on one page.
Rationale: Existing MM-418 work already unified the page layout.
Alternatives considered: Rebuilding the layout was rejected because behavior is already present.
Test implications: final validation only.

## FR-002 / FR-003

Decision: implemented_verified.
Evidence: `sourceKind` controls Registry Manifest and Inline YAML fields in `frontend/src/entrypoints/manifests.tsx`; existing tests submit registry and inline modes through existing APIs.
Rationale: The source modes and action field already match MM-419.
Alternatives considered: Adding a separate page or modal was rejected because `/tasks/manifests` is the canonical page.
Test implications: final validation only.

## FR-004 / DESIGN-REQ-002

Decision: implemented_verified.
Evidence: separate `manifestName`, `manifestContent`, and `registryName` state in `frontend/src/entrypoints/manifests.tsx`; existing test verifies registry title after editing inline fields.
Rationale: Independent state preserves source-mode values during switching.
Alternatives considered: Shared field state was rejected because it would lose mode-specific drafts.
Test implications: final validation only.

## FR-005 / DESIGN-REQ-004

Decision: implemented_verified; validate before side effects.
Evidence: `frontend/src/entrypoints/manifests.tsx` rejects nonblank `maxDocs` values unless they are positive whole numbers; `frontend/src/entrypoints/manifests.test.tsx` verifies invalid max docs produces an error and no manifest API call.
Rationale: MM-419 requires non-positive and non-integer values to be rejected before submit.
Alternatives considered: Relying on backend validation was rejected because the client previously omitted invalid values, preventing backend rejection.
Test implications: focused frontend validation and runner-integrated UI validation.

## FR-006 / DESIGN-REQ-004

Decision: implemented_verified; use a small client-side raw secret-shaped value detector before side effects.
Evidence: `frontend/src/entrypoints/manifests.tsx` scans source helper fields and inline YAML before submitting; `frontend/src/entrypoints/manifests.test.tsx` verifies raw secret-shaped values are rejected while env-style references remain allowed.
Rationale: MM-419 requires UI rejection before submit or env/Vault references instead.
Alternatives considered: Importing backend Python validation into frontend is impossible; adding a small frontend detector aligned with existing secret-like patterns is sufficient for client-side prevention.
Test implications: focused frontend validation and runner-integrated UI validation.

## FR-007 / DESIGN-REQ-005

Decision: implemented_verified.
Evidence: successful submit sets an in-page notice, exposes an `Open run` link, and calls `refetch`; existing tests assert in-place refresh.
Rationale: Behavior already satisfies the success flow.
Alternatives considered: Navigation to run details was rejected because the desired behavior is in-place context.
Test implications: final validation only.

## FR-008 / DESIGN-REQ-006

Decision: implemented_verified.
Evidence: tests locate controls by visible label or role (`Source Kind`, `Registry Manifest Name`, `Manifest Name`, `Inline YAML`, `Action`, `Run Manifest`).
Rationale: Existing semantic form controls are accessible enough for this story.
Alternatives considered: Custom segmented controls were rejected as unnecessary for this runtime gap.
Test implications: final validation only.

## FR-009 / DESIGN-REQ-007

Decision: implemented_verified.
Evidence: submit action is outside the collapsed `details` advanced-options region in `frontend/src/entrypoints/manifests.tsx`.
Rationale: The primary action is visible in default form flow.
Alternatives considered: Pinning the button was rejected as unnecessary for the current layout.
Test implications: final validation only.

## FR-010

Decision: implemented_verified.
Evidence: MM-419 appears in `docs/tmp/jira-orchestration-inputs/MM-419-moonspec-orchestration-input.md`; `specs/216-run-manifest-page-form/spec.md` preserves the original preset brief verbatim in `Input`.
Rationale: Final verification must explicitly preserve issue-key traceability.
Alternatives considered: Reusing MM-418 spec was rejected because it would lose MM-419 traceability.
Test implications: final MoonSpec verification.
