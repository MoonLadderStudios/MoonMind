# Declared framework packs

`security.assess` accepts an operator-supplied framework pack through the same
artifact admission, Skill invocation and report path as native security evidence.
There is no remote registry, scheduler or source download. Every built-in pack
(GLI-19/GSF, New Jersey DGE, PCI DSS v4.0.1, ISO/IEC 27001,
[SOC 2](SOC2Readiness.md), UKGC RTS and OWASP ASVS 5.0.0 Level 1) ships as an
installed declared pack, so built-in and operator packs use `frameworkPacks` and
the shared requirement evaluator, and any of them can be combined in one plan.

## Selecting packs

Read `GET /api/v1/settings/framework-packs` for the installed readiness packs,
the security-review Skill's web/API methodology and declared packs registered
in saved environments. Each entry describes its edition, rights, supported
subjects, evidence, conclusion limits and input schema. Unavailable entries
include a reason. The entry with `choice: general-best-practices` is the general
best-practices choice for applications (OWASP ASVS 5.0.0 Level 1); its
`companionChecks` name the shipped checks to run with it and their presets. A missing effective date is unknown. Publication of a
readiness pack does not establish that a jurisdiction adopted the standard.
Each readiness preset in Create is a thin selection of one catalog entry: it
names the installed pack by `framework.packId` and shows typed fields generated
from the pack's declared profile, with no free-form JSON editor. None is
retired, because each keeps prompts specific to its standard; all of them run
the same `security.assess` path as a direct request. The
[security plan](SecurityPlans.md#planning-from-create) selects several catalog
entries at once.

Settings can register a declared pack using its exact artifact reference, ID,
edition, revision and SHA-256. Catalog reads use the caller’s artifact access
and recheck any source documents the pack cites.
Restricted registrations expose identity and summary metadata. Their licensed
text stays behind the artifact boundary. A saved choice supplies context.
Assessment admission checks the selected bytes, rights and period again.

Supply up to 16 selections in `frameworkPacks`. Each names its `artifactRef`,
`packId`, `edition`, `revision`, `sha256` and assessment `period` (`start`,
`end`). An installed pack omits `artifactRef` and `sha256`: the assessment
retains the installed bytes as an artifact and cites that exact content. Use
each pack ID and edition pair once. The singular `frameworkPack` form in saved
requests and schedules selects one pack through the same evaluator. A saved
request, preset, rerun or schedule that passes `framework.packId` for an
installed pack is normalized once at the request boundary to the currently
installed edition and revision, so saved work follows the installed pack without
edits: `systemInputs` becomes `profile`, saved `requirementEvidence` moves to
each requirement's choice as `organisationalEvidence`, and `evidenceRefs`,
`auditRef` and `previousAssessmentRef` carry over unchanged. A saved shape the
pack cannot read is refused at admission.
Key each selection’s optional `requirementChoices` by requirement ID. A choice
can narrow `systems` and `period`, and records `applicability` plus `applicabilityReason`. Omitted
applicability remains undetermined. Explicit applicable/not-applicable choices
need a rationale; `notApplicableReason` is also accepted. Choices cannot expand
admitted assets or drop exclusions and repository exceptions.

A request-level `previousAssessmentRef` compares all selections against one
retained control report. A selection can supply its own previous report. All
selections compile into one plan and are mutually exclusive with
`controlAssessment`. Selected packs need no native tool; without one, declared
objective checks stay incomplete. A pack and its crosswalks are not evidence
that checks ran.

The artifact owner verifies access and stored bytes before any native collection.
The declared ID, edition and revision must match the selected values, and the
SHA-256 must match the exact input bytes. Source documents the pack cites must be
readable with the same access and still match their recorded digests; a missing,
unauthorized or changed source refuses the assessment. These are evidence-integrity
checks, not deployment, image or runtime compatibility requirements.

## Pack contract

Use `schemaVersion: declared-framework-pack/v1` with:

- `id`, `title`, `edition`, `revision` and optional `amendments`. Identifiers are
  bounded; mutable edition/revision aliases such as `latest` are refused.
- `source`: a `section`, a `reviewedOn` date and either an HTTP(S) `url` or, for
  an operator-owned document, its `artifactRef` and `sha256`. An artifact source
  may add `excerptSha256`, the digest of the cited text, so a reworded statement
  is detectable without copying it. URL fetching is never implied. One artifact
  keeps one digest throughout the pack. `provenance`, `reviewedBy` and `reviewedOn`
  retain attribution, not independent verification of authorship or assessor
  qualification. Only a draft may omit `reviewedBy` and `reviewedOn`.
- `publicationStatus`: `published`, `draft` or `superseded`. Published material
  needs `publishedOn` and `effectiveFrom`; optional `effectiveUntil` is exclusive.
  Draft, superseded, future and expired packs are explicitly refused for new
  operative assessments. Assessment periods must fit the effective interval.
  Historical reports keep their original edition and conclusions.
- `license` and `distribution`: `redistributable`, `restricted` or `prohibited`.
  The operator must have the relevant rights. Declaring a license or writing an
  original summary does not establish permission to redistribute source text.
  Optional `attribution` is the notice reproduced requirement text must carry;
  retained metadata and the readable report repeat it beside the pack.
- One to 512 `requirements`, each with `id` (published section names may
  contain spaces), `title`, original `summary`,
  `category`, `applicability`, `evidencePrompt`, `objectiveBasis`, and explicit
  `objectives`. Objectives use `id`, `description`, `source`, `tool` and `ruleIds`.
  `ruleIds: ["*"]` selects every rule of a source whose coverage declares all its
  configured rules, for scanners whose rule IDs are advisory identifiers;
  `observation: true` keeps exposure facts for review without passing or failing
  the requirement. `objectivesComplete` defaults to false. `requiredEvidenceMethods` can name
  `deterministic_check`, `enquiry`, `evidence` or `observation`; optional
  `evidenceTypes` further describes needed material. Requirements can also retain
  their own `sources`, `module`, `subjectTypes` and responsibility descriptions;
  a security plan with a `network` subject selects only requirements whose
  `subjectTypes` include `network`.
- `externalClaim` on a requirement names a pack external claim: the requirement is
  external-only, declares no checks, and its plan names who can close it.
  `organisationalEvidence` lists requests (`id`, `category`, `purpose`,
  `description`) the pack raises while the requirement applies. Supply revisions
  under the same `id` in `requirementChoices`; category and purpose stay the pack's.
- `notAssessedReason` marks a requirement MoonMind has no evidence path for. It
  declares no checks, procedures or requests, and an applicable one concludes
  `not_assessed` with that reason.

A pack may also declare readiness content, each part omitted when unused so
existing packs keep their digests:

- `profile`: typed inputs the operator supplies (`text`, `boolean`, `number`,
  `date`, `choice`, `choices`, `text_list`, `jurisdiction_basis`, or
  `structured` with a `model` whose record shape the pack's behavior registers,
  such as ISO/IEC 27001 risks or UKGC critical systems). A `role` gives a field shared
  meaning: `entity_type`, `product_type`, `systems` (bounds requirement scope),
  `jurisdiction_basis` (adoption with source, record version and dates),
  `applicability_basis` and `selection` (selectable requirements). A `required`
  field names the `requestId` and `prompt` raised when it is missing.
- `modules`: separately edited sources with `edition`, `source` and
  `sourceStatus` (`reviewed` or `Unverified` with its reason).
- `applicableWhen` on a requirement: condition sets over boolean, choice and
  multi-choice profile fields, any of which applies; a multi-choice input matches
  when any recorded choice is expected, and a missing input (including an empty
  required one) leaves applicability Unverified with that field's
  `unknownReason`. `applicabilityText` supplies the applicable, not-applicable
  and not-selected reasons; `citeRule` appends each requirement's own
  applicability statement to its not-applicable reason.
- `behavior`: a registered judgement that cannot be data: GLI module
  applicability by entity and product, New Jersey annual-assessment records with
  approval, operator and incident qualifiers, the ISO/IEC 27001 Statement of
  Applicability with its risk-treatment links, SOC 2 period-aware sampling, and
  the UKGC audit record with auditor independence. A behavior can own a
  requirement's whole conclusion when the judgement is more than evidence
  matching; scope, period and choice checks stay shared. Unknown behaviors are
  refused.
- `externalClaims`: assurances only an outside party can give, each with `gap`,
  `verifiableBy` and `nextStep`. They are always reported Unverified.
- `exclusionReview`, `evidenceGuidance`, `limitations`, `sector`,
  `jurisdiction` and `conclusions` for catalog and report presentation.

Only completed deterministic checks currently establish objective satisfaction.
Enquiry, document evidence, observations or unknown evidence types withhold
completeness and produce a diagnostic, even if all scanner checks pass. Rights
and independent assessor review remain Unverified when not independently evidenced.

## Readiness evidence for profile packs

A selection for a pack with a `profile` adds `profile` inputs, scoped
`evidenceRefs` and, when its behavior reads one, an `auditRef` record. Profile
`systems` must fit the admitted assets. Applicability comes from the profile:
incomplete inputs give incomplete or Unverified applicability and an evidence
request, never a guess. `notApplicableReason` records an exclusion (reviewed
when the pack sets `exclusionReview`); `responsibleEntity: partner` keeps the
requirement visible as `partner_owned` until its evidence is supplied.

Each evidence record uses the scoped record fields (`kind`, `requirementIds`,
`systems`, `period`, `method`, `description`, `provenance`) plus optional
`evidenceType` (`technical`, `organizational`, `game_math`, `rng`,
`laboratory`, `regulator` or `external_audit`), `outcome`, `edition` and
`jurisdiction`, and the qualifiers a behavior reads. A typed record supports a
requirement only when it passed, names the pack edition and the module standard
(`GLI-GSF-2 v1.0`), uses a kind allowed for its type, covers the systems and
period, and, for regulator evidence, the recorded jurisdiction. External audits
also need reliance covering the period. Records are read once per assessment;
a record may name requirements of several selected packs, and each pack judges
only its own, so no conclusion transfers. Review evidence the same assessment
collects (identity lifecycle, change control) is judged the same way.
Current documentary revisions in `requirementChoices` are also read once, then
reviewed by every pack that cites them: ISO/IEC 27001 beside an organisation's
own ISMS policy pack can cite one adopted-policy revision, and each pack
concludes for its own requirements.

Requirement readiness is one of `evidence_recorded`, `incomplete`,
`partner_owned`, `excluded`, `exclusion_for_review`, `not_applicable`,
`not_selected`, `applicability_missing` or `Unverified`. Recorded evidence is
not an executed check: such requirements conclude `inconclusive` (or
`not_assessed` when not applicable), never `satisfied`. A profile pack's
objectives are partial checks on the requirement that names them: a current
finding concludes it `not_satisfied`, and a clean scan leaves it `inconclusive`.
Certification and jurisdiction approval are `not_performed`.

Every compiled declared-pack requirement carries an `evidencePlan`: its methods
and evidence types, its number of MoonMind checks, its procedure IDs, and, for an
external-only requirement, the claim and the party who can close it, or for a
not-assessed one, the reason. The readable report lists it beside each conclusion.

## Directional mappings

`mappingTargets` explicitly catalogs each destination `id`, `frameworkId`,
`edition` and `requirementIds`. `mappings` names `fromRequirementIds`, `targetId`
and `toRequirementIds`, allowing one-to-many and many-to-many relationships.
Each mapping has an `id`, `relation` (`equivalent`, `partial`, `subset`, `superset`
or `related`), `rationale`, `source`, and `basis` (`upstream` or `evaluated`).
Evaluated mappings require `reviewedBy` and `reviewedOn`. Direction runs from the
pack's requirements to the named destination edition. Broken references and
ambiguous duplicate editions fail validation. Conflicting source interpretations
remain separate attributed mappings, never automatic coverage or satisfaction.

A mapping can start from finding classes instead of requirements: `fromFindings`
lists `source`, `tool`, shell-style `rulePatterns` and, optionally,
`vulnerabilities: true` for findings marked as known vulnerabilities. A target
with `reportingLens: true` (and optional category `titles`) is a reporting lens:
the control report groups that pack's requirement conclusions and the run's
current findings by the target's categories, each with its mappings'
attribution. A category is `assessed`, `not_assessed` (no assessed requirement
maps to it) or `unmapped`; none is reported as passing, the lens adds no coverage,
and findings no class matches are listed separately. Only redistributable
metadata carries mappings, so a restricted pack has no lens.

## Reports and retention

The ordinary `controlAssessmentRef` and readable report retain pack identity,
revision, integrity, effective dates, intelligence digests and each source
document's artifact reference and SHA-256. Redistributable
packs also retain the declared source, review, license, requirement and mapping
metadata. Restricted operator-supplied packs are retained behind the artifact
owner's existing access boundary; portable reports omit their titles, summaries,
source sections, rationales and objective descriptions, with an explicit omission
diagnostic. Source documents stay required dependencies of the report that cited
them. Prohibited packs are refused. Retention does not grant sharing rights
or permission to export the underlying licensed artifact. A profile pack's
metadata also keeps its `readiness` record: profile, applicability, modules,
evidence, requests, unresolved requirements and external claims. Its evidence,
provenance, adoption and audit artifacts are required dependencies of the report.

The combined plan admits 1,024 requirements, 16,384 asset-by-rule cells
and 64 distinct supporting and organisational evidence references, including
revision history. Each declared pack must fit in 2 MiB and the structured
control report in 8 MiB. Web/API methodology admits 512 planned objectives and
512 procedure results.
Native facts for a shared source, tool, rule and asset are evaluated once.
Each requirement applies its own period, applicability and evidence conditions.

The control report seals each selected artifact and summarizes conclusions and
coverage by pack. The 1 MiB readable presentation lists each conclusion ahead
of budgeted detail and links omitted details to the structured record.
`previousAssessmentRef` reads the prior sealed record once even when several
selections cite it. New packs start with an initial comparison. Packs found
only in the previous report are marked Unverified. Missing or unreadable
history leaves comparison Unverified. A change in revision,
objectives, rights or profile inputs is reported as an intelligence change.
Both export tools return an explicit unsupported result for a multi-pack run.

A retained report written before a built-in moved to this contract
(`gli-readiness/v1`, `iso-27001-readiness/v1`, `soc2-readiness/v1`, or New Jersey
or UKGC `framework-audit-preparation/v1`) still renders, exports and serves as
`previousAssessmentRef`. A temporary reader compares matching requirement IDs
(renamed statuses such as `unassessed` count as `incomplete`), marks pack
revision, digests and missing requirements Unverified, summarises the previous
report in the new one, and lets evidence export include it. Nothing writes these
schemas any more; remove the reader once none remains in retention.

## Packs from your own policy

The `security-policy-pack` preset turns an organisation's own policy or ISMS
documents into a declared pack. Upload the documents as text or Markdown, which
the default attachment policy admits, attach them to the drafting step, and state the
policy name, owner, and version or approval date. Select the saved environment so
its evidence-processing limits apply: when model delivery is disabled, local-only
or limited to other Provider Profiles, the run is refused before an agent starts
and no document content is sent. The portable Skill treats document text as
untrusted data and writes a `policy-pack-draft-response/v1` artifact. It never
sets status, distribution, review or completeness fields; the contract refuses them.

`POST /api/v1/settings/framework-packs/drafts` then checks that response outside
any model step. Supply the policy identity, each document's `artifactRef` and
`sha256`, the response reference and digest, and optionally `ruleInventoryRefs`
(retained `security-observation/v1` records), a `mappingTarget` naming an installed
standard and its exact edition, and a `previousPack`. A requirement is kept only
when its locator names a supplied document (by artifact or SHA-256), a section,
clause or page in it, and a quoted sentence found there that uses shall, must or
required. Missing locators, quotes not at the cited place, guidance offered as a
requirement and text addressed to a model or workflow are flagged and left out.
Unsupported or unreadable files are listed by name. A normative sentence becomes
an open question unless the quotes of kept rows at its document and section cover
most of it, so a bare "must" or a refused row never hides one. A suggested objective survives
only when the inventory shows that exact source, tool and rule ran as a
deterministic check; otherwise it is flagged as invented or unverifiable. Mapping
proposals must name requirements of the selected standard.

The call returns a `policy-pack-draft/v1` artifact and a Markdown review sheet,
both under the caller's artifact access. The draft pack is `restricted` and
`draft`, so it is never operative and cannot be selected for an assessment. Its
requirements cite the document artifact, digest, location and excerpt digest,
hold no objectives, and need organisational evidence or agent review.

`POST /api/v1/settings/framework-packs/accept` is the review step, also outside any
model step. Name the draft (`draftArtifactRef`, `draftSha256`) and a
`projectId`/`environmentId`, and give `review`: `reviewedBy`, `reviewedOn`,
`effectiveFrom`, optional `effectiveUntil`, `publishedOn`, `distribution`,
`removeRequirementIds`, `requirementEdits`, `adoptObjectiveSuggestions` and
`acceptMappingProposals`. Source documents are rechecked first. The result is a
new published pack artifact registered in that saved environment in place of the
same pack's earlier registration, so the catalog lists it. The revision is pinned
with its source documents sealed as required dependencies, so it stays readable
past ordinary expiry; unpin a revision you no longer register. Adopted rule suggestions
never complete a requirement's objectives. Accepted mappings become `evaluated`
with the reviewer's attribution. To edit an accepted pack, pass its identity as
`pack` instead of a draft; that creates the next revision.

To handle a policy revision, draft again from the new document with the operative
pack as `previousPack`. The draft proposes the next revision and lists added,
removed, reworded and unchanged requirements, those whose applicability, evidence
or objectives changed (a redraft carries no adopted objectives), and source
documents that changed.
An unchanged sentence keeps its earlier requirement ID. Earlier revisions and the
assessments that used them are never rewritten. Drafting quality depends on the
model and the document, so the accept step stays the operator's review.

## Organisational evidence requests and imports

For `security.assess`, expand **Organisational evidence requests and review** in
Create's selected Tool inputs. Declare requirements in `controlAssessment`, or
choices in `frameworkPack.requirementChoices`, then add requests to the selected
requirement. Import a non-empty document or bounded structured JSON file (up to
4 MiB) with the existing artifact upload service. Copy the completed `art_`
reference into a revision in the ordinary structured input or JSON editor. The
upload stays in the artifact store; importing does not accept its contents.
Explicit revision references are admitted to the selected workflow before it
starts, separately from image attachments. This does not admit model delivery. Failed
uploads preserve the file selection for retry. Existing authorized references can
be reused without uploading again.

Each requirement/choice has an `organisationalEvidence` list. A request records
`id`, `category`, `purpose` (`design`, `adoption`, or `occurrence`), `description`,
`owner` (a label, not a role), and `revisions` (empty means requested). Categories
cover risk registers/treatment, statements of applicability, policies, training,
access reviews, incidents, suppliers, internal audits, management reviews and
corrective actions. Each revision records:

- `artifactRef`, `kind` (`document`, `operational_record`, `management_assertion`,
  or `generated_draft`), `source`, `collectedAt`, `expiresAt`, `sensitivity`
  (`public`, `internal`, `confidential`, or `restricted`), `population` (asset
  labels), and `period` (`start`/`end` dates).
- Optional `review`: `outcome` (`accepted` or `insufficient`), `purpose`,
  `reviewer`, `reason`, `reviewedAt`, `population`, and `period`. This is an
  attributed review of the cited revision, not independent event verification.

Use existing security-review Skills to inspect authorized evidence and cite the
revision when recording the reason. A policy-design review does not establish
adoption; adoption and occurrence require operational records. Generated policy,
SoA or minutes drafts and management assertions remain insufficient substitutes
for those records. Missing, unreadable, expired, future-dated, out-of-period or
incompletely populated evidence remains an explicit gap. Read access and temporal
sufficiency are rechecked on every assessment, including retries. `document_review`
is distinct from deterministic scan results and cannot replace executed checks.

Reuse a reference in several requirements with separate purpose/period reviews.
Append a new revision to replace evidence; earlier references and their reviews
remain superseded history. Completed assessments are immutable, so reviewing the
same evidence for a later period creates another assessment rather than changing
an earlier review. Only current revisions are admitted, read and retained as
current evidence; superseded references remain metadata without new read grants.
Earlier reports retain their own evidence closure. Current artifacts follow
existing required-dependency retention. There is no new document store, email automation,
role system or approval engine. Sensitivity labels describe evidence; existing
artifact authorization and model-delivery admission still govern access and use.
Real organisation evidence and external assessor conclusions remain Unverified
unless independently obtained; controlled synthetic evidence tests this journey.

## PCI DSS v4.0.1 readiness

Select `pci-dss-readiness` edition `4.0.1` in `frameworkPacks`. It lists the 12
principal requirements, their defined requirements and Appendices A1 and A2 as
identifiers with original summaries; consult a licensed copy of the standard for
its text, testing procedures and guidance. Published 11 June 2024, it is
effective from 31 March 2025, when the future-dated requirements came into force,
so 6.4.1 and 10.7.1, superseded on that date by 6.4.2 and 10.7.2, are not listed.
Appendix A3 applies only to entities a payment brand or acquirer designates and is
not included. A later edition arrives as a new pack revision, which history
comparison reports as an intelligence change; superseded, draft, future and
expired copies are refused with the pack, edition and dates in the reason.

The profile records `entityType` (`merchant`, `service_provider` or
`multi_tenant_service_provider`; choose a provider type when the entity is also a
merchant), `systems` (the CDE and connected or security-impacting systems),
`cardholderDataEnvironment`, `panStored`, `sadBeforeAuthorization`,
`supportsIssuing`, `paymentChannels`, `posPoiEarlyTls`, `validationRoute` (ROC
or SAQ type), `serviceProviders` and `scopeBasis`. Service-provider, multi-tenant,
stored-data, issuer, card-present, e-commerce and early-TLS requirements apply
only when the profile says so, and each exclusion names its rule. Record a
provider-owned requirement with `responsibleEntity: partner` and the provider in
`responsibilityReason`; it stays `partner_owned` until its evidence is supplied.

Principal requirements, ASV scans (11.3.2) and penetration and segmentation
testing (11.4.2 to 11.4.6, A1.1.4) are external-only: the plan names the QSA or
ISA (or the acquirer-accepted SAQ), the Approved Scanning Vendor or the
independent tester. Partial checks support only named sub-requirements: image
and dependency vulnerabilities, with KEV and EPSS priority, for 6.3.3; secret
detection for 8.6.2; TLS for 4.2.1; selected Prowler AWS checks for 1.3.1, 8.3.6,
8.4.1, 8.4.2, 10.2.1 and 10.3.4; and, as review-only observations, ZAP alerts for
6.2.4, open TCP services for 1.2.5 and DNS names for 12.5.1. Prowler's own PCI
mappings stay review-required candidates, including for another edition.
Policies, roles, training, incident response, targeted risk analyses,
customized-approach documentation and periodic reviews are organisational
evidence requests. Results are readiness evidence only. PCI SSC distribution
rights, a check of the identifiers against a licensed copy, and compliance
validation by a QSA, ISA or acquirer remain Unverified external claims.

## OWASP ASVS 5.0.0 Level 1 (general best practices)

Select `owasp-asvs-l1` edition `5.0.0` revision `1`, or choose general best
practices in the catalog. It lists all 70 Level 1 requirements of the ASVS 5.0.0
release under their upstream identifiers (`V1.2.1` and so on) with their text.
`tools/build_asvs_pack.py` generates it offline from the release's
machine-readable file, pinned by tag `v5.0.0_release`, commit and SHA-256, plus
MoonMind's classification in `tools/asvs_l1_classification.json`; `--check`
confirms a rebuild reproduces the installed bytes and the digests in
`PROVENANCE.json`. Levels 2 and 3 can be built from the same file and are not
offered until their evidence paths are reviewed.

The requirement text is OWASP content under CC BY-SA 4.0. It lives with the
release file, its license, a notice and the provenance record in
`moonmind/security/packs/owasp-asvs/`, apart from the Apache-2.0 source, and the
pack's `attribution` travels into every report of it. Edit the classification,
not the generated pack, and rebuild.

Each requirement declares how MoonMind can evidence it. V1.3.2 has the only
installed-rule check: MoonMind's default Opengrep eval rules, which fail it on a
finding and pass on a clean complete run but never complete it. Most
requirements need agent review or reproduction through procedures: the six
security-review web/API procedures, embedded unchanged by ID for the
requirements they address (that Skill's `web-api.json` stays their single
owner and a build input), and a requirement review elsewhere. Four documentation
requirements raise organisational evidence requests, and V14.3.1 is not
assessable here because it needs a browser observed after logout. No requirement
declares complete objectives, so a clean scan satisfies none; procedure results
stay in the methodology report with their tested, partially tested, blocked or
untested states. Applicability starts undetermined: record each requirement's
applicability in `requirementChoices`.

OWASP Top 10:2025 is the pack's reporting lens. Mappings from requirements are
`upstream` where a Top 10 page cites an ASVS 5.0.0 chapter (A01 V8, A02 V13, A03
V15, A04 V11, V12 and V14) and otherwise `evaluated` against the page's mapped
CWEs. Finding classes place known vulnerabilities from Trivy repository and image
scans (with KEV and EPSS priority where feed data exists) under A03, Gitleaks and
passive-scan secrets under A07, Trivy configuration checks under A02, the default
Opengrep rules under A05 and TLS findings under A04. A09 and A10 cite ASVS V16,
which has no Level 1 requirement, so the report states them unmapped.

The general best-practices choice composes this pack with known-vulnerability,
secret and misconfiguration scanning from the `security-code-supply-chain` and
`security-image-sbom` presets; supply their retained summaries in `reportRefs`.
OWASP does not certify ASVS conformance, and MoonMind makes no conformance claim.

## General best practices for networks

Select `network-best-practices` edition `2026-11` for a network
[security plan](SecurityPlans.md). Its eleven requirements are MoonMind's own
original statements, attributed in the pack, not benchmark or vendor text.
NET-1 to NET-5 are complete objectives over the offline
[network configuration analyzer](NetworkConfigAssessment.md), NET-6 to NET-10
need agent review of the sanitized exports, and NET-11 needs the operator's
approved configuration standard. PCI DSS Requirement 1, ISO/IEC 27001 A.8.20 to
A.8.22 and GLI-GSF-1 GIS-5 and GIS-8 are also tagged `network`.

## ISO/IEC 27001 ISMS readiness

Select `iso-27001-readiness` edition `2022+Amd1:2024`, or use the
`security-iso-27001` preset. The pack identifies the ISO/IEC 27001:2022
management-system clauses and 93 Annex A references with Amd 1:2024 climate
changes, using original readiness prompts rather than licensed text. Consult an
appropriately licensed copy for the complete normative requirements.

The profile records `systems`, `scopeStatement`, `context`, `interestedParties`,
`climateRelevance`, `risks` and `controls`. A risk has an `id`, `version`,
description, owner, assessment, treatment, `treatmentPlan`, `controlIds` and
`residualRiskApproval`; accepting a risk also needs `acceptanceReason`. Missing
treatment and approval records remain actions rather than inferred success.

Key `controls` by reference such as `A.8.8`; necessary additional controls use an
`EXTRA.` identifier and a title. Each decision retains `decision` (include,
exclude or undetermined), rationale, `riskIds`, owner and declared
`implementationStatus`. An exclusion without rationale, or of a control linked
to current risk treatment, stays unresolved. A scanner mapping is never needed
to retain a necessary control. Management-system clauses stay distinct from
Annex A and cannot be excluded. Annex A scope, responsibility and documentary
evidence go in `requirementChoices`; an additional control is not a pack
requirement, so its decision records its own `systems`, `period`, responsibility
and `organisationalEvidence`.

Each management clause needs current evidence of its own category and purpose;
an internal audit does not establish management review. Annex A controls need
design, adoption and occurrence evidence separately. Internal audits,
management reviews, training and corrective actions need records of occurrence;
a policy draft or management assertion does not prove they happened. Missing,
denied, expired, out-of-population or wrong-purpose evidence remains a gap.
`evidenceRefs` can reuse scoped technical records for several requirement IDs
as context (`technicalContextRefs`); technical context never replaces the
organisational record.

The readiness record keeps the draft Statement of Applicability with a version
derived from the scope, period and decisions, each decision's rationale,
implementation assertion, evidence status and risk links, and the risk-treatment
actions. The readable report names the SoA version. With `previousAssessmentRef`,
changed risk, scope, period, responsibility or pack identity names the affected
controls and requirements for review, and a management-only change flags only
that clause; the earlier report is never edited.

Readiness describes recorded evidence only. Generated policies and SoAs remain
drafts, a passed vulnerability retest cannot close missing ISMS process evidence,
and certification and actual organisational operation remain **Unverified**
external claims.

## UKGC RTS security-audit preparation

`ukgc-rts-security` (edition `ISO-IEC-27001-2022`, the control set RTS section 4
selects) is described in [UKGC RTS audit preparation](PassiveRepoScan.md#ukgc-rts-audit-preparation).
Its behavior derives annual-audit applicability, audit targets and reporting
conditions from the profile, needs enquiry, evidence and observation on every
in-scope critical system, and reads the audit record from `auditRef`. The
independent audit and regulator decisions remain Unverified.
