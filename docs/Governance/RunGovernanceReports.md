# Per-Run Governance Reports

**Document Class:** Canonical declarative
**Viewpoint:** Cross-Cutting Concept
**Owners:** MoonMind Engineering
**Last Updated:** 2026-09-07

> [!NOTE]
> This document defines the desired-state per-run governance report contract
> (MoonMind#3969, child of #3930). It is a declarative contract for what one
> inspectable report per run contains, where each field comes from, and what
> the report explicitly does not claim. Rollout and backlog notes live under
> `docs/tmp/` or in gitignored local-only handoffs, not as migration
> checklists in this file.

## 1. Summary

Each run produces one inspectable governance report built from existing
authoritative evidence. The report is not a second audit or event system, not
a compliance certification, and not guaranteed complete visibility into
arbitrary runtime activity.

The report is a versioned JSON document (`contract_version = 1`,
`report_kind = "run_governance_report"` in
`moonmind/governance/run_reports.py`). The human-readable Markdown rendering
is generated from the same JSON; there are never two independent reports.

## 2. Contract

Identity: logical workflow id, run id, attempt, creation time, evidence
cutoff, deterministic input digest, and idempotent report id
(`govrep_<sha256>` over identity plus input digest). Retrying the same input
digest reuses the stored result. Late or corrected evidence writes a new
immutable version with an explicit `supersedes` relation; the original is
never overwritten. Logical-run and attempt relationships survive continuation
and recovery.

References: policy, profile, and image refs with digests where the owning
system provides them. Source artifact digests pin every evidence input.

Sections (all bounded, all behind allowlisted fields): credential
leases/generations with run-owned vs profile-owned ownership, observed egress
decisions, container jobs, approvals/reviews, outbound scans, workspace
changes, publications, cleanup disposition, and optional spend/usage.

## 3. Provenance states

Every section carries its owner and evidence source with one of
`observed`, `unavailable`, `unsupported`, `not_applicable`, or `pending`.
Missing data is never rendered as zero actions, no secrets, approval granted,
or successful cleanup. Cleanup defaults to `unknown`/`pending`, scans default
to `unavailable` (never `pass`), and reviews default to `unavailable` (never
`approved`). Model-generated explanation may be attached as an optional
annotation and is never authoritative audit evidence.

## 4. Redaction and bounds

Raw credentials, credential-home contents, prompts, transcripts, sensitive
URLs, and unbounded command/file content are excluded by field allowlisting,
not by regex redaction after the fact. Only authorized references (artifact
refs, lease refs, redacted locations) are serialized. Section cardinality is
bounded (credentials 32, other sections 50, digests 64). Detailed evidence
stays behind protected artifacts with retention and access rules; the report
carries refs and digests only.

## 5. Terminal coverage

Reports are finalized for `succeeded`, `failed`, `cancelled`, and `timed_out`
executions where evidence exists. A crash before normal finalization is
covered by a bounded reconciler (`reconcile_missing_reports`) that emits
retryable work items for existing worker/schedule infrastructure; it starts
no new always-on service. Reporting failure is auxiliary: it returns a
visible recoverable status, preserves the canonical task/publication outcome,
never prevents cancellation, and never suppresses credential/host release.

## 6. Presentation

Workflow Detail links the report under the evidence surface with status
`ready`, `partial`, `pending`, or `failed`, a safe explanation, and an
authorized artifact-backed download (never a raw URL). Historical reports stay
interpretable after host removal or profile rotation: they reference evidence
as observed at the cutoff, expose no current credentials, and never claim
unresolved cleanup succeeded.

## 7. Verified observation boundary and blind spots

Observed: MoonMind-mediated egress decisions, bounded native outbound-scan
evidence as defined by `docs/Security/SecretsSystem.md`, run-owned vs
profile-owned credential state from the shared-host materializers, container
job lifecycle, approvals/reviews recorded by the platform, workspace changes
and publications with artifact refs, and cleanup disposition as observed at
the evidence cutoff.

Not observed (explicit blind spots): arbitrary agent network/shell actions
outside mediated boundaries; binary attachments, terminal input, and browser
automation outside the text-scan contract; provider-internal activity not
exposed through provider-reported measurements. Spend/usage fields, when
present, distinguish `provider_reported` measurements from `estimate` values
and `unavailable` account-wide data.
