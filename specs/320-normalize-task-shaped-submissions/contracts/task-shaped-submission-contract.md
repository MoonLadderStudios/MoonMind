# Contract: Task-Shaped Submission Normalization

## Scope

This contract describes the public control-plane behavior for create, edit, and rerun task submissions. It is intentionally implementation-agnostic and covers the request shape that Mission Control and API clients submit before execution starts.

## Accepted Task Shape

The task submission accepts:

- objective instructions
- objective-scoped input attachment refs
- ordered steps
- step-scoped input attachment refs
- runtime intent
- publish mode and publish options
- canonical branch intent
- dependencies
- Jira provenance where supported
- authored preset binding metadata
- applied template and step provenance metadata

## Attachment Targeting

- Objective attachments are submitted only as task-level attachment refs.
- Step attachments are submitted only on the owning step.
- Each attachment target must be explicit and valid.
- A step reorder preserves each step attachment with the owning step.
- Text edits, preset application, preset aliases, and migration-era input do not retarget attachments silently.
- Mixed valid and invalid target declarations fail explicitly.

## Branch Semantics

- New task-shaped submissions use one canonical task branch field for authored branch intent.
- New normalized task output does not emit a separate target branch field.
- Publish mode determines whether the canonical branch means a branch update or pull request workflow.

## Provenance Semantics

- Jira provenance is preserved where the task contract supports it.
- Authored preset binding metadata is preserved when supplied.
- Applied template metadata and step provenance are preserved when supplied.
- Provenance payloads must remain compact and must not embed large external data.

## Failure Behavior

Submissions fail before execution receives normalized task data when they contain:

- invalid repository values
- unsupported runtime values
- unsupported publish mode values
- invalid dependency declarations
- disabled or violated attachment policy
- missing, unknown, conflicting, or ambiguous attachment targets
- unsupported attachment metadata fields
- binary content embedded in instruction fields instead of structured refs

Failures must be explicit and user-visible enough to correct the authored task.

## Verification Surface

Contract tests should cover:

- valid create submission with objective and step attachments
- valid edit submission preserving target bindings
- valid rerun submission preserving snapshot-backed target bindings
- canonical branch output without target branch aliases
- preset and Jira provenance preservation
- negative validation for invalid repository, runtime, publish, dependency, attachment policy, and target binding
- retargeting attempts through reorder, text edits, preset aliases, and migration-era input
