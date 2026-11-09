# Passive Repository Scan

**Document Class:** Tool/report note
**Viewpoint:** Security tooling
**Status:** Implemented
**Owners:** MoonMind Engineering
**Last Updated:** 2026-09-22

Passive scans and retained-artifact assessments use the existing Container Job,
Temporal and artifact owners. See [Security Tools and Assessment Workflows](SecurityTools.md)
for supported tools and target admission.

## Scanner selection

The in-repo `moonmind.security.outbound_scan.scan_outbound_text` regex scanner
runs with `high_security_mode=True` through `moonmind/security/passive_repo_scan.py`.
It emits category/location/redacted-preview findings; raw matches never leave
the boundary. `PassiveScanConfig` records file-count, per-file and total byte
bounds. Results include native JSON and a Markdown summary. This in-process
MoonMind code requires no scanner image, external feed or target-network access.

## Execution

- **Supported journey:** `moonmind container passive-scan` submits through
  `passive_scan_submission()` / `run_passive_scan_job()` in
  `moonmind/container_job_cli.py`. Managed-session admission stamps workspace
  and correlation identity. The command waits durably and prints terminal state,
  logs and native report/summary references.
- **Input:** an authorized snapshot directory (declared input class:
  UTF-8 text files). The default snapshot is the mounted workspace root
  (`.` with `workdir: /workspace`): the container-job path mounts the
  authorized repository directly at `/workspace`. A snapshot path that is
  itself a symlink is rejected before resolution, so container files outside
  the authorized workspace are never inspected. Symlinks escaping the
  snapshot, binary content, undecodable files, special files, and bound
  overruns are recorded as skipped and force an `incomplete` verdict --
  never clean. Repository-control metadata (`.git`) is excluded from
  working-tree coverage without forcing `incomplete`.
- **Job:** `build_scan_job_workload()` declares the production workload:
  existing `moonmind-python-tests` image source, `networkMode: none`,
  bounded CPU/memory/PIDs/timeout, and two declared outputs
  (`artifacts/passive-scan-report.json`,
  `artifacts/passive-scan-summary.md`). The workspace mount stays writable
  so the declared outputs are collectable. The scanner avoids source writes
  and writes only the two declared outputs; this is implementation discipline,
  not an operating-system-enforced read-only source mount. The broader
  security-tools design separates protected inputs from writable outputs.
  Workspace and correlation identity are
  stamped by the existing `container_job_submission` path; the entrypoint is
  `python -m moonmind.security.passive_repo_scan`. File enumeration is
  incremental and stops as soon as the `max_files` bound is exceeded.
- **Feed:** none. The offline regex mode needs no external feed; result
  metadata records `feed: {name: none}` plus the tool ref, input digest, and
  configuration identity (never a compatibility fingerprint).

## Results

- **Verdicts:** `finding_present` | `clean_with_coverage` | `incomplete`.
  Zero findings cover only the listed scanned files; they never mean the whole
  repository is secure. Malformed, truncated, timed-out, cancelled, stale, or
  skipped data always resolves to `incomplete` (`parse_scan_report_json`
  fails closed). A `clean_with_coverage` verdict additionally requires the
  complete evidence schema (tool identity, 64-hex input digest computed over
  canonical untruncated paths, non-empty coverage with matching counts,
  recorded configuration and feed, and a non-empty summary); a bare
  `{"verdict": "clean_with_coverage"}` payload is `incomplete`. Retained
  configuration, feed, and cancellation fields round-trip instead of being
  replaced by defaults.
- **Detection:** the maintained outbound-scan contract (bare credential keys)
  plus a bounded repository supplement for prefixed/suffixed credential
  assignments (`DATABASE_PASSWORD`, `GITHUB_TOKEN`,
  `AWS_SECRET_ACCESS_KEY`). Supplement findings carry the key name plus a
  redaction marker, never raw values.
- **Sensitivity:** source and findings are untrusted and potentially
  secret-bearing. Locations and previews are redacted via
  `redact_sensitive_text`, path-validated, and bounded. Reports carry no raw
  secret matches and invent no verification.
- **Retention:** `retain_scan_report()` stores the native JSON and the summary
  through the existing artifact store, so the result stays readable after job
  cleanup. Source fixes or publication are separate authorized work, never a
  scan side effect.

## Limitations

Deterministic regex shapes only; this job does not provide dependency/inventory,
live-cloud, or network-active application assessment. It performs no external
uploads. Missing dependencies produce a clear
`incomplete`/unsupported result rather than widened authority.

## Recurring assessments and retained comparisons

`security.assess` is a native tool for existing manual and recurring workflows.
It compares retained passive reports, or reassesses an inventory against an
explicit advisory feed without a source-code change. Temporal still owns
cadence, overlap, catch-up and cancellation. The tool does not launch an agent
or require model triage.

Optional [OSCAL exports](OscalExport.md) use these native records and restricted
report artifacts. Full assessment and remediation documents require explicit
system/SSP, procedure, party and action context; export diagnostics leave native
reports usable.

Create a schedule with `POST /api/recurring-workflows`, using the normal
`MoonMind.UserWorkflow` target and a step with `type: "tool"`:

```json
{
  "id": "assess",
  "type": "tool",
  "tool": {
    "name": "security.assess",
    "inputs": {
      "scope": {
        "assets": ["app"],
        "exclusions": [],
        "repositoryExceptions": []
      },
      "tools": ["inventory-feed"],
      "budget": {
        "baseSeconds": 60,
        "maxSeconds": 120,
        "progressStallSeconds": 60
      },
      "credentialRefs": [],
      "networkRefs": [],
      "inventoryRef": "art_inventory",
      "feedRef": "art_feed",
      "baselineRef": "art_previous_comparison"
    }
  }
}
```

The example refs stand for completed, authorized artifacts. Declare inputs in
the workflow's `inputAttachments` as `{"artifactId": "art_..."}` so the existing
occurrence admission links them to that execution. Each invocation checks
current artifact read authority before reusing scanner work. A saved schedule
confers no credential or target-network authority. Assessment requests keep
empty credential/network reference lists; GitHub alert import uses the run's
admitted repository connection, and active adapters must use their existing
admission owners. Agent-backed steps and PR finishing, including
`review_only`, retain execution-plan admission; an assessment adds no image or
source-revision pin.

Use existing overlap/catch-up policy and idempotent manual runs. Assessment
authorizations optionally bound continuing work by minimum interval, occurrence
count, per-occurrence jobs/seconds and run lifetime. Original validity, scope
and aggregate limits still apply. Canonical reservations survive schedule edits,
retries and restarts; catch-up cannot compress cadence. Worker launch and passive
reassessment recheck authority. Credential resolution stays with its workload
owner: `CredentialGrant.revision` identifies the secret, not a policy revision.
Passive work acquires no network rights.

Renew/revoke through the recorded rules panel in Recurring or Workflow Detail.
After material changes, edit the schedule, review current authority, use the
proposal, then save. Equivalent runs need no extra confirmation. Renewal does
not reset consumption. The occurrence panel links retained comparisons;
missing/expired evidence is a gap. Existing comparisons explain scope, tool,
pack/feed and coverage changes, never treating disappearance alone as remediation.

Supply new feeds and previous comparisons through normal authoring. Feed
snapshots reuse `container.run_job`; no second poller is introduced. Confirmed
scans survive report retries; cancellation starts no successor. External provider
qualification remains Unverified without run evidence.

### Evidence contracts

The versioned JSON contracts live in `moonmind/security/assessments.py`:

- `security-inventory/v1`: scope, observation/expiry timestamps, configuration
  reference, coverage status and components (`asset`, unversioned `purl`,
  `version`). Producer-asserted completeness covers the declared scope,
  including assets with no components.
- `security-feed/v1`: source, revision, observation/expiry timestamps and native
  rules (`id`, `purl`, explicit `affectedVersions`). The bounded adapter matches
  exact versions; it does not guess version ranges or fetch intelligence.
- `security-observation/v1`: native source/tool/rule/asset/instance identity,
  evidence refs, coverage, configuration/feed provenance and optional assessed
  product `deployment`. The
  `retained-report` tool consumes these observations through `reportRefs`; it
  also accepts this document's passive scanner JSON, using snapshot-relative
  file paths as scope assets. Native passive evidence expires for comparison
  after one day; its artifact retention follows the roots that seal it.
- `security-comparison/v1`: the observation, retained findings, scoped triage,
  counts, baseline/scan artifact references and optional `productComparison`.
  Finding identity never depends on a presentation title or severity.

### Requirement assessments

`security.assess` evaluates explicit `controlAssessment.requirements` or a
[declared framework pack](FrameworkPacks.md) after native scans and comparison.
The ordinary report publishes `security-control-assessment/v1` as
`controlAssessmentRef`. Mappings and an empty findings list do not satisfy a
requirement.

Requirements identify their framework/edition, title, scope, period and justified
applicability. Each objective names explicit `source`, `tool` and `ruleIds`.
`objectivesComplete` defaults to false; declaring a complete checklist requires
an `objectiveBasis`. Scope cannot expand admitted assets or drop exclusions and
repository exceptions. Optional `owner` and `reviewer` are attributed metadata,
not MoonMind roles or authenticated opinions.

Satisfaction requires every selected asset/rule check to have complete, current,
explicitly deterministic evidence covering the declared objectives. Coverage
rows record `evidenceMethod`; an unclassified legacy record or interpretation
cannot become an executed check. The inventory/feed and passive source adapters
identify their deterministic checks, as do RNG statistical diagnostics; RNG
implementation review stays interpretation. Other unclassified adapters remain
inconclusive for this purpose until their producer identifies the actual method.
`allRules` does not prove an arbitrary mapped rule was installed. Conflicting,
failed or incomplete matching observations remain visible. Observed native defects
produce `not_satisfied`; accepted risk stays separate triage with its existing
author, reason, review and expiry semantics.

A single-date period means a point-in-time observation on that date, not
continuous operation for a day. Multi-day operation cannot be established by
one snapshot. Per-coverage `coverageWindows` retain each source's observation
and expiry through aggregation and baseline reuse; refreshing an advisory feed
does not refresh the inventory population's observation date. Its `validFrom`
also requires the selected feed to be current. Legacy aggregated
records without source windows remain inconclusive instead of borrowing a
newer source's timestamp.

Optional `supportingEvidence` entries name an authorized `artifactRef`,
`method` (`agent_interpretation` or `management_assertion`), `position`
(`supports`, `contradicts`, or `context`), `objectiveIds`, `scope`, `period`,
`author`, `reason`, and optional `expiresAt`. These are attributed claims,
not executed checks, and cannot replace missing native evidence. Missing,
denied, expired, out-of-scope or contradictory required context prevents a
satisfied conclusion. A requested unavailable `reviewRef` remains explicit.

Each result separates applicability, coverage, conclusion, evidence sufficiency,
review state, methods, exceptions, limitations and next actions. Justified
non-applicability is not satisfaction. One artifact can support several
requirements without sharing their conclusions. The implementation bounds
requirements, check populations, references and evidence bytes; it performs no
new network operations or scanner launches.

Read authority, freshness and decisions are reevaluated before immutable control
results are reused. The control artifact seals its scan, comparison and readable
context through the existing dependency owner. Unreadable refs remain diagnostics,
not verified dependencies; readable but invalid interpretations retain their bytes
without becoming usable evidence. Control or publication failure preserves native
outcomes; retries reuse completed scans. Changed conclusions do not rewrite
historical artifacts. Independent assessor conclusions and certification remain
Unverified; an authorized assessor can review the retained scope, objectives,
methods and evidence without being an implementation-completion gate.

For assessed gaming deployments, supply `productBaselineRef` separately from
the finding-history `baselineRef`. It names a `security-product-baseline/v1`
artifact with `product` (an admitted scope asset), `baselineId`, `state`,
`regulatorApprovalRequired`, `components`, and supporting `records`. States
`submitted`, `evaluated`, `certified`, `approved`, and `regulator_approved`
remain distinct from the observed deployment; matching a submitted image
cannot supply approval. Records bind their `stage`, `product`, and `baselineId`
to `status`, `evidenceRefs`, `reason`, and `verifier`. Readable retained evidence
supports a recorded comparison, not independent authentication of approval.
Missing, denied, unreadable, or URL-only records remain Unverified with reasons.

An observation's `deployment` carries `product`, `deploymentId`, `components`,
and `omissions`. Each component has `id`, `kind`, `identity`, `method`,
`evidenceRefs`, and optional `omissionReason`. Kinds cover artifacts, game
configuration, paytables, feature settings, critical-file signatures, RNG
configuration, external configuration, and database elements. Baseline
`excludedKinds` records reasons for non-applicable categories; undeclared
categories and missing critical evidence make the comparison incomplete.
External settings are compared individually even when the image is unchanged.

Select a newer baseline explicitly through `productBaselineRef`; it does not
rewrite old observations or repeat a saved scan. `approvedChanges` entries name
an `id`, replacement `component`, and scoped approval `record`. Only applicable
recorded approvals change the expected identity; proposed changes retain the
baseline and report the gap. Structured and readable reports retain matches,
drift, incomplete evidence, methods, reasons, and omissions through retry and
authorized artifact download. Identity equality establishes no system security,
certification, regulatory approval, or compatibility, and never controls MoonMind
launches or updates. Private lab/regulator authenticity remains Unverified.

Comparisons distinguish `new`, `still_observed`, `absent_comparable`,
`remediated_verified`, `closed_upstream` and `not_reassessed`. Absence requires
fresh, completed coverage of the original asset/rule under comparable scope,
exclusions and configuration. Comparable absence becomes `remediated_verified`
only with a passing `fix_retest` decision for the finding; a source's own fixed
state (a GitHub alert GitHub reports fixed) also counts, while dismissed or
resolved alerts are `closed_upstream`. Coverage rows record the feed, rule and
tool identity that interpreted the inputs (`intelligence`). New or absent
findings whose covering tool's intelligence changed list the keys in
`changedIntelligence`, and the comparison lists them in `intelligenceChanges`.
A changed code-scanning analyzer or version makes GitHub absence incomparable. Partial scans,
failed authentication, unsupported assets, expired evidence and changed scope
retain prior findings and evidence. Large evidence unions remain reachable
through retained input reports. An unavailable or malformed baseline, including
missing stored bytes, is explicitly unavailable; findings then use
`observed_without_baseline` and cannot claim to be new or resolved. Malformed
current reports yield incomplete coverage while preserving earlier evidence.

An optional `decisionsRef` contains a JSON array of triage decisions. Each
decision names `findingId`, `disposition`, `reason`, `evidenceRefs`, `author`,
`source`, `scope`, `reviewAt` and `reviewConditions`, with optional `expiresAt`.
Artifact ids (`art_...`) in `evidenceRefs` must be readable; other references,
such as a tracker URL, stay text.
Dispositions are `accepted_risk`, `false_positive`, `confirmed_vulnerability`
and `fix_retest`; only the latter requires `retestOutcome` (`passed`, `failed`
or `inconclusive`). Due or expired decisions re-enter review, as do unrenewed
decisions whose finding's intelligence or the observation's provenance changed,
and passing retests the scanner contradicts by still reporting the finding.
`reviewReasons` names which (`review_due`, `expired`, `review_pending`,
`intelligence_changed`, `provenance_changed`, `retest_contradicted`). Review
persists until a renewed decision for the finding replaces it; replaying the
same decision cannot clear it. Earlier decisions remain in baseline artifacts.
Decisions annotate observations and never change scanner facts. Repository exceptions remain explicit scope
configuration.

The existing report links expose a readable delta/coverage/review summary,
structured comparison and saved scanner evidence. `scanOutcome` and
`publicationOutcome` remain separate: a publication failure returns the
durable refs and diagnostics without changing the scanner outcome. Live
cloud/model qualification is separate from these controlled artifact journeys.

Before a scan, comparison or summary is reused or published, the producer
seals what it cites through the artifact
[dependency owner](../Temporal/WorkflowArtifactSystemDesign.md#143-lifecycle-manager).
A scan seals the input and native report bytes it read. A comparison seals its
scan, its baseline's bytes, earlier evidence it still cites whose digest the
baseline's seal verified, and its `decisionsRef` with the artifacts those
decisions cite. Product comparisons also seal the selected product baseline
and readable supporting records; deployment evidence is retained by the scan.
Unavailable records remain references and diagnostics, without blocking report
retention or export. Carried decisions keep their evidence through the baseline's
seal, which also lets an occurrence in a later workflow seal that history
without gaining a read of it.
A summary seals its comparison and evidence. Review evidence is optional for
native comparisons; an explicitly requested control assessment retains its
readable review context as a required dependency. Missing, unauthorized, corrupt or
mismatched required evidence fails the seal, and a retry keeps an existing
seal. While a root is pinned, unexpired or held by a saved schedule's input
root, cleanup cannot remove what it seals; releasing the root returns that
evidence to its own retention. The baseline edge keeps that comparison only,
not its lineage, so recurring history does not accumulate.

A root published without a seal has unverified, not empty, history. Lifecycle
backfill, or admitting it as a baseline, seals it from its own schema with its
producer's authority: a scan when its inputs reproduce the recorded work
identity, a comparison when its scan and any baseline are sealed. Already
expired evidence is not certified; its root stays unsealed and observable, and
is unavailable as a baseline.
Cleanup visits all backfill pages before expiring evidence, so a later retained
root can still seal it; committed seals survive a restart between pages.
GitHub alert scans, scans reused from a baseline, comparisons built on them and
report text stay unsealed and follow their own retention.

### Imported GitHub alerts

The `github-alerts` tool imports the repository's existing code-scanning,
Dependabot and secret-scanning alerts. Select it with `"githubAlerts":
{"repository": "owner/name"}`; optional fields are `ref` (`refs/heads/...`,
default branch when omitted), `revision` (the assessed commit, else the ref's
tip at import), `categories`, `state` (`open` or `all`), `includeAnalyses`, and
`perPage`/`maxRequests`/`maxItems`/`maxBytes`/`maxSeconds` bounds for the whole
import. `maxSeconds` must fit the base budget.

Reads use the run's admitted repository connection with the read-only
`security_read` operation (GitHub App: `security_events`,
`vulnerability_alerts` and `secret_scanning_alerts` read, plus `contents` read to
resolve the assessed revision). A connection without
it sends no request and reports every category `authorization_unavailable`; no
ambient token is substituted. The import only issues GETs on that repository's
endpoints, follows `Link` continuations only on the same endpoint, retries
transient failures within the request budget and keeps pages read before a
later failure. It never dismisses alerts, changes settings or enables products.

Each category records `complete`, `permission_denied`, `product_disabled`,
`product_unavailable`, `rate_limited`, `repository_unavailable`,
`pagination_incomplete` (with its continuation), `malformed_response` or an
upstream/transport failure. Only `complete` can be complete coverage; the
others are `authentication_failed`, `unsupported`, `partial` or `failed`, and
the projection lists them as Unverified with a next verification step.

Findings use source `github`, tool `github-<category>`, the alert number as
native identity, and the rule, advisory or secret type. Only open alerts at the
assessed revision and on a scoped file path (or the scoped `owner/name` asset)
become findings. Code-scanning alerts use their latest instance commit,
secret alerts their first detected location, and Dependabot the default-branch
tip; an alert at another revision is listed as such. A category is complete only
when its observed revision is the assessed one. The sanitized
`github-security-alerts/v1` evidence keeps allowlisted identity, state,
dismissal/resolution, location and analysis fields, never secret values. A
retry in the same run reuses that import; a later run rereads GitHub.

Imported and local findings both remain. Correlation, saved with the scan,
matches only the same asset plus an advisory or rule identifier the local tool
reports (Dependabot with Trivy, or a code-scanning tool of the same name) and
records closed-upstream/observed-locally and open-upstream/absent-locally
conflicts. Secret alerts are not matched, since no shared identity survives
sanitization.

### GitHub change-control periods

Select `github-change-control` in `security.assess` with an explicit period and
repository/environment scope:

```json
{
  "githubChangeControl": {
    "repositories": ["owner/repository"],
    "environments": ["production"],
    "since": "2026-11-01T00:00:00Z",
    "until": "2026-11-06T00:00:00Z",
    "requirementIds": ["CC8.1", "8.32"],
    "sampleSize": 25
  }
}
```

Include each repository in `scope.assets`. Credentials come from the existing
run admission's `read` operation; selection never grants access to another
repository. A hidden, denied or unadmitted selected repository remains an
explicit coverage gap. Rulesets and classic branch protection are collected
separately; classic protection requires `administration:read`. Denied or missing
reads remain coverage gaps, without broader authority or a substitute token.
It issues only GETs and publishes no issues, PRs, comments or repository changes.

The period includes `since` and excludes `until`. Closed PRs are enumerated and
filtered by merge time; default-branch commits use commit time, which does not
establish when a push occurred. Selected environments' deployment records and
statuses are enumerated separately. Status contexts, check names and workflow
names/IDs identify verification on PR heads and merge commits. A production link
requires a matching merge/deployment commit, the provider's production flag, and
a successful deployment status in the period after merge. Approved merges
without that evidence retain an unresolved production link. Rulesets, classic
review/check requirements, reviews and verification are collection-time
snapshots, not historical control proof. Missing historical deployment statuses
remain a retention limitation.

`perPage` (100), `maxPages` per endpoint (20), `maxRequests` (200), `maxItems`
(1000), `maxBytes` (4 MiB) and `maxSeconds` (60) bound collection across the
selection. `maxSeconds` must fit the assessment's base budget. Each response is
bounded to 512 KiB. Retries reserve requests and bytes before sending; after an
interrupted exchange the reserved bytes remain charged unless its saved page
can reconcile the response. Restart reuses saved pages, the original deadline
and consumed budgets. Throttling/transient retries are bounded to three attempts
per page; lengthy throttles, missing pages, unavailable endpoints and exhausted
bounds produce incomplete coverage while retaining collected evidence.

The restricted report retains a separate source population and deterministic
sample with its population reference, selection rationale and coverage limits.
Exceptions explain emergency labels, unresolved reviews/deployments, deployments
without a period PR and possible direct pushes; an absent PR association alone
cannot prove a direct push. Source pages retain query scope, collection time,
identities and timestamps, excluding PR/review bodies and workflow logs. Artifact
dependency seals retain the sample, population and sources together. The
population stores verification once per repository/commit, referenced by changes
and deployments. Above 4 MiB it is retained as ordered binary parts through a
`github-change-control-population-manifest/v1` artifact; concatenate `partRefs`
to read the complete population, including every source page reference. The emitted
`FrameworkEvidence` record carries caller-selected SOC 2/ISO control IDs and feeds
the existing framework preparation path as `technical_assessment` evidence;
collection does not establish compliance. Live-account and historical-population
comparisons require a separately authorized read-only account and known source
period; synthetic integration covers the supported implementation journey.

## UKGC RTS audit preparation

Choose **UKGC RTS Security Audit Preparation** in the shared preset catalog or
pass `framework` to `security.assess`. Both paths use the same scoped evaluator
and retained report bundle. The pack selects the 44 ISO/IEC 27001:2022 controls
listed in [RTS section 4](https://www.gamblingcommission.gov.uk/manual/remote-gambling-and-software-technical-standards/4-remote-gambling-and-software-technical-standards-rts-security-requirements),
including cloud security (5.23) and security testing (8.29); other Annex A controls
are not added to the RTS requirement set.

The Create fields accept saved system inputs, an audit period (`start` and
`end` dates), evidence artifact IDs, an optional audit-record artifact, and
requirement choices keyed by control ID. Omitted choices use the full admitted
system scope and period; a choice can set `systems`, `period`, or
`notApplicableReason`. Markdown and JSON reports retain these choices for
auditor review.

System inputs describe `operatorName`, `businessDescription`, `licenceClass`,
`systemIdentification`, and `criticalSystems`. Each critical-system entry names
one category (`customer_information`, `outcome_rng`, `gambling_state`,
`entry_exit`, or `communications`), `systems`, `inScope`, and a `rationale`.
Record absent categories explicitly. `thirdParties` records their `name`,
`systems`, `responsibilities`, and retained `operatorResponsibilities`.
The system identifiers must fit the assessment's admitted `scope.assets`.

Listed remote operator and host classes require annual audits. The
[audit advice](https://www.gamblingcommission.gov.uk/licensees-and-businesses/guide/security-audit-advice)
includes real-event betting classes beyond the testing-strategy footnote; the
report preserves both sources. `software_supplier`, `telephone_only` and
`trading_room` do not establish a listed annual-audit obligation. Lottery classes
need `annualLotterySalesGbp`; the threshold is strictly greater than £250,000.
Unknown classes or missing scope produce actionable requests, not an exemption.

Each evidence artifact contains `kind`, `requirementIds`, `systems`, `period`,
`method` (`enquiry`, `evidence`, or `observation`), a description of documents,
staff, versions and samples, and `provenance` (`sourceRef`, `issuer`, `version`,
`recordedOn`, `standard`). Separate records can cover each system; every
selected system needs all three methods for the chosen period. A prior
`external_audit` also needs auditor-reviewed `reliance` with `reviewedBy`,
`reason`, `reviewRef` and `validFor`. Work against
another standard also needs an explicit `equivalenceReason`. Its coverage
and provenance remain in the package. `iso_certificate` and
`technical_assessment` records supply context without completing audit elements.
An unstructured uploaded document prompts for a scoped evidence record.

The optional audit artifact records auditor name, firm, background,
qualifications, `independent` and an `independenceRef` artifact; `auditedOn`,
`locations`, `executiveSummary`, `overallOpinion`, and `findings` with control ID,
classification, description, management response, action owner and target date.
Missing independence, observation, or management plans remain explicit gaps.
Observation can use documented remote inspection; documentation alone is
insufficient. References to retained evidence are authorized and sealed by the
existing artifact service.

`licenceGrantedOn`, `lastAuditOn` and `regulatorDueOn` record first and annual
timing. A delayed first audit uses `operationsStartedOn` only when
`firstAuditExtensionApproved` is recorded. Calculated six-month and annual dates
are planning targets; the Commission-set date remains explicit. Optional request
and receipt dates expose reporting targets. The preparation package records
retention, seven-day response conditions and immediate major-nonconformity
notification advice. It performs no submission. `prepared` means supplied
preparation records cover the selected elements; an actual independent audit or
private inspection remains **Unverified**. No human-role system is involved.

## GLI interactive-gaming readiness

Choose **GLI-19 and GLI-GSF Readiness** in Create (`security-gli-readiness`), or
pass `framework.packId: gli-19-readiness` to `security.assess`. The preset uses
the same evaluator, artifact authorization, comparisons and report export.
Saved preset inputs retain assessed systems, jurisdiction basis, requirement
choices and evidence references.

The pack records [GLI-19 v4.0, revised November 3, 2026](https://gaminglabs.com/wp-content/uploads/2026/11/GLI-19-v4.0-Standards-for-Interactive-Gaming-Systems.pdf),
GSF-1 common controls v1.1, and GSF-2 technical assessment, GSF-3 vendor controls
and GSF-5 online controls v1.0. Each section-level readiness requirement has
an original summary, source section, evidence category and assessed-business
responsibility. These summaries identify the examined readiness scope; they
do not replace the full standards or laboratory evaluation criteria.

`systemInputs` records `entityName`, `entityType` (`operator`, `rgs`, or
`ancillary_provider`), `productType` and `systems`. The system identifiers must
fit the assessment's admitted `scope.assets`. Operators and RGS products using
`interactive_gaming` select GLI-19 and GSF-1/2/5. Ancillary providers using
`ancillary_service` select GSF-1/2/3 only when `directlyAffectsGaming: false`;
unknown impact remains unresolved. [GSF-3](https://gaminglabs.com/wp-content/uploads/2024/06/GLI-GSF-3-v1.0-Gaming-Information-Security-GIS-Vendors-Audit-FINAL.pdf)
does not automatically apply to an RGS. `gaming_device` and `event_wagering`
require the GLI-11 and GLI-33 product profiles respectively; this pack reports
that boundary without claiming product coverage from shared security controls.

Edition publication is independent of a jurisdiction's adoption and a product's
approval. `jurisdictionBasis` contains `jurisdiction`, `status` (`unknown`,
`adopted`, or `not_adopted`), `edition`, `adoptedOn`, `effectiveOn`,
`recordVersion`, `sourceRef` and an explanatory `reason`. No real jurisdiction's
adoption is assumed. Missing provenance, a different adopted edition, or dates
that do not cover the assessment period leave applicability incomplete. A
record marked `not_adopted` remains visible as comparison information.

Set `period.start` and `period.end`. `requirementChoices`, keyed by IDs such as
`GLI-GSF-2:B` or `GLI-19:5.2`, can select `systems` and a different `period`.
Omitted choices use all assessed systems and the assessment period. Explicit
`notApplicableReason` exclusions stay visible. For partner-owned duties, set
`responsibleEntity: partner` and `responsibilityReason` identifying the partner
and agreement; absent evidence is still requested. These responsibilities
describe the assessed business, not MoonMind accounts or human roles.

Each `evidenceRefs` artifact uses the shared scoped evidence record: `kind`,
`requirementIds`, `systems`, `period`, `method`, `description` and `provenance`
(`sourceRef`, `issuer`, `version`, `recordedOn`, `standard`). GLI also requires
`evidenceType` (`technical`, `organizational`, `game_math`, `rng`, `laboratory`,
or `regulator`), `outcome` (`passed`, `failed`, `incomplete`) and GLI `edition`.
`standard` identifies the cited module and edition, for example
`GLI-GSF-2 v1.0`. Regulator evidence also names `jurisdiction`. Technical results
use `technical_assessment` or scoped `control_evidence`; laboratory and
regulator records use `external_audit`. An interview alone cannot satisfy a
technical, game/RNG, laboratory or regulator requirement. Link existing security,
game-math and RNG observations through `provenance.sourceRef`; native artifact
references are authorized and sealed alongside the evidence record. An uploaded
document without this scope produces a request for a usable record.

Matching evidence is reported as `evidence_recorded`. A clean vulnerability
scan cannot fill operational, mathematical, RNG or laboratory gaps. `prepared`
means supplied evidence covers the selected readiness requirements; certification
and jurisdiction approval remain `not_performed`. Organizational, laboratory
and regulator verification remain **Unverified**, with the evidence owner, gap
and next action recorded even in a complete synthetic fixture.

The retained JSON and Markdown exports include edition, module selection,
jurisdiction basis, scope, exclusions, partner responsibilities, evidence links,
unresolved requirements and external verification. Reassessments can supply
`previousAssessmentRef` to link the previous retained framework report. Edition,
adoption-record and evidence changes produce new result identities while earlier
reports remain unchanged; these assessment editions never pin an agent runtime.

## New Jersey DGE readiness

Choose **New Jersey DGE Readiness** in the shared catalog, or use
`framework.packId: nj-dge-readiness` with `security.assess`. Saved presets,
requirement-specific systems/periods, evidence requests, and restricted JSON and
Markdown exports use the same path as the UKGC pack. No submission is transmitted.

Set `systemInputs.entityName`, `entityType`, `systems` and `applicabilityBasis`.
The basis identifies the licence, content contract and system inventory used to
choose scope. `casino_licensee` needs `offersInternetGaming` for
`nj.internet.annual` ([13:69O-1.2(q)](https://www.law.cornell.edu/regulations/new-jersey/N-J-A-C-13-69O-1-2));
`nj.network.annual` applies to casino-licensee networks
([13:69D-2.4(e)](https://www.law.cornell.edu/regulations/new-jersey/N-J-A-C-13-69D-2-4)).
For `rgs_content_provider`, `nj.rgs.annual` applies only when
`providesContentToOtherOperator` is true
([13:69O-3.1(a)(4)](https://www.law.cornell.edu/regulations/new-jersey/N-J-A-C-13-69O-3-1)).
`unrelated_supplier` inherits none of these annual obligations. Missing activity
inputs leave applicability Unverified. Omitted `annualRules` evaluates all three;
an explicit list limits the annual rules selected, with unselected rules visible.
These are annual system-integrity and security assessments, not an expressly
specified penetration-test-only obligation.

`auditRef` names an artifact containing `kind` (`synthetic` or `external`) and
`assessments`. Each entry has an annual `requirementId`, `scope`, `systems`,
`period`, `assessors` (each with `name`, `affiliation`, `independent` and an
`independenceRef` artifact), `assessedOn`, `findings` (an explicit empty list means
none), `correctiveActions`, `licenseeResponse` and `opinion`. Record why corrective
action is unnecessary when appropriate. `professionalEvidenceRef`,
`divisionApprovalRef`, `reportRef` and `submissionRef` identify evidence of
professional suitability, approval of the selected assessor, the report and its
annual submission. Missing fields are requested individually; a clean scan
cannot supply an independent professional opinion.

Other requirement IDs are `nj.rgs.internal-controls`, `nj.rgs.games-releases`,
`nj.rgs.incidents`, `nj.rgs.transactions`, `nj.rgs.integration` and
`nj.partner.player-accounts`. Evidence artifacts use the scoped record fields
described above, with `kind: control_evidence` and method `evidence` or
`observation`. Add `divisionApprovalRef` for controls and games/releases,
`operatorEvidenceRef` for transactions, integration and partner-owned accounts,
and `incidentDisposition` (`events_reported` or `no_reportable_incidents`).
Reported incidents also need a `notificationRef` covering operator and Division
notifications. Identify the partner and responsibility boundary in the evidence
description. Certificates, technical assessments and generic audit records never
supply these missing approvals or partner records. All supporting artifact
references are authorized and retained with the report.

Reports snapshot the selected source publications, amendment notes, review date,
entity basis, systems, periods and evidence. The source snapshot is not a DGE
technical-standard version. The [DGE rule index](https://www.njoag.gov/about/divisions-and-offices/division-of-gaming-enforcement-home/new-jersey-casino-control-act-and-regulations/)
and [Technical Services Bureau](https://www.njoag.gov/about/divisions-and-offices/division-of-gaming-enforcement-home/slot-laboratory-tsb/)
remain references for current codification and guidance; direct retrieval was
unavailable at the recorded review. Source completeness and GLI adoption remain
Unverified: the historical CHAPTER69O PDF alone is insufficient, and no newest
GLI edition is assumed adopted. Later source or evidence updates create a new
preparation without changing retained reports. `prepared` describes recorded
evidence coverage. Private assessor, customer/operator and Division verification
remains **Unverified**, including for complete synthetic fixtures.

## Composable security review

The resolved `security-review`, `security-investigate` and `security-remediate`
Skills cover direct threat modeling, business-logic/authorization review,
repository assessment, triage, bounded hypotheses and authorized fix/retest.
They use the selected Omnigent harness, Provider Profile, model and original
budget. Investigation and remediation declare the review Skill as a required
sibling, so its portable procedures and upstream license/provenance travel in
the same resolved bundle for Codex, Claude Code and OpenCode.

Direct logic review remains useful without a scanner. A Skill records examined
code, routes, identities and resources in a separate `security-review/v1`
artifact; it never rewrites native findings. Its interpretations carry evidence
refs, confirmed/likely/unresolved dispositions, supported/contradicted/unsupported
evidence status and explicit limits. Contradicted or unsupported claims remain
unresolved. Retest records retain the original hypothesis, before/after evidence,
changed assets and covered scope. These agent descriptions cannot attest runtime
confinement, test success or a resolved scanner finding.

Pass that artifact's `reviewRef` to an existing `security.assess` step. With no
scanner inputs, use `tools: []`, the explicit `reviewRef`, the admitted scope,
empty offline credential/network refs and the remaining budget. The native tool
checks scope, referenced artifact access and native finding identities, then
links the interpretation and renders its threat model and limitations alongside
the native summary. Missing scanner coverage stays incomplete. Review validation
or report failure preserves completed scan/comparison artifacts; changing only
`reviewRef` does not repeat scanning. Native comparisons remain independently
readable without inference.

Use registered security operations and the existing scoped ContainerJob path
for tool evidence and focused tests. Repository/report/tool instructions grant
no authority. An assessment-only request permits review artifacts but cannot
authorize source edits, credential changes, deployment or source publication.
An already authorized assessment/fix/test/publication request continues through
the existing workspace and publisher without another approval ceremony. Retest
the original hypothesis and changed scope; changed rules, failed scans and
reduced coverage cannot prove a fix.
