# Repository conventions and escalation

Discover `AGENTS.md`, `README.md`, the docs index and nearby owner documents that
actually exist. Read only guidance relevant to the target and its claim type.
Missing MoonMind paths in another repository are not a blocker and do not justify
inventing a taxonomy. Preserve its casing, locations, metadata, ownership, claim
IDs and requested issue keys. Do not infer ownership from age or file size.

In a MoonSpec repository, read the classification/precedence sections of
`docs/Workflows/MoonSpecDocumentModel.md`, then the relevant sections of
`docs/DocumentationArchitecture.md`: §7 for conflicting owners, §8 for rationale,
§11 for metadata, and the selected viewpoint in §3 and template in §15 for
new canonical documents. Do not load every viewpoint template. Temporary work
belongs under `docs/tmp/` or the repository's gitignored artifact location.

## Escalation handoff

Preserve a structured record with `document`, `claim`, `evidence` (paths, relevant
excerpts or test receipts), `owningDecision` (owner candidates and the exact
choice required), `reason`, and `resumeCondition`. Write it to a caller-specified
artifact path, otherwise `artifacts/document-escalations.json`, with a readable
summary. Complete independent authorized edits while holding the affected claim.

Only use a tracker when the caller already authorized that external mutation and
an integration is available. Discover its actual project/type/required fields;
never assume Jira, a project key or a credential. Follow the selected integration's
Skill from the active bundle. Include a provider-neutral `trackerIssue` containing
`provider`, `key`, `url` and the verified creation/read receipt only after success.
A missing or denied tracker leaves the full local handoff intact. Do not retry a
denied mutation through broader credentials or another provider. Tracker receipt,
document update, implementation verification and publication are distinct outcomes.
