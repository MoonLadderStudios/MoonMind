# Declared framework packs

`security.assess` accepts an operator-supplied framework pack through the same
artifact admission, Skill invocation and report path as native security evidence.
There is no remote registry, scheduler or source download. Bundled preparation
packs use `framework`. Declared packs use `frameworkPacks` and the existing
requirement evaluator. [SOC 2 readiness](SOC2Readiness.md) uses the shared preset
catalog, documentary evidence owner and private reports.

## Selecting packs

Supply up to 16 selections in `frameworkPacks`. Each names its `artifactRef`,
`packId`, `edition`, `revision`, `sha256` and assessment `period` (`start`,
`end`). Use each pack ID and edition pair once. The singular `frameworkPack`
form in saved requests and schedules selects one pack through the same evaluator.
Key each selection’s optional `requirementChoices` by requirement ID. A choice
can narrow `systems` and `period`, and records `applicability` plus `applicabilityReason`. Omitted
applicability remains undetermined. Explicit applicable/not-applicable choices
need a rationale; `notApplicableReason` is also accepted. Choices cannot expand
admitted assets or drop exclusions and repository exceptions.

A request-level `previousAssessmentRef` compares all selections against one
retained control report. A selection can supply its own previous report. All
selections compile into one plan and are mutually exclusive with
`controlAssessment`. Native evidence tools or an explicit review are still required. A pack and its crosswalks are not evidence that checks ran.

The artifact owner verifies access and stored bytes before any native collection.
The declared ID, edition and revision must match the selected values, and the
SHA-256 must match the exact input bytes. These are evidence-integrity checks,
not deployment, image or runtime compatibility requirements.

## Pack contract

Use `schemaVersion: declared-framework-pack/v1` with:

- `id`, `title`, `edition`, `revision` and optional `amendments`. Identifiers are
  bounded; mutable edition/revision aliases such as `latest` are refused.
- `source`: an HTTP(S) `url`, a `section`, and `reviewedOn` date. URL fetching is
  never implied. `provenance`, `reviewedBy` and `reviewedOn` retain attribution,
  not independent verification of authorship or assessor qualification.
- `publicationStatus`: `published`, `draft` or `superseded`. Published material
  needs `publishedOn` and `effectiveFrom`; optional `effectiveUntil` is exclusive.
  Draft, superseded, future and expired packs are explicitly refused for new
  operative assessments. Assessment periods must fit the effective interval.
  Historical reports keep their original edition and conclusions.
- `license` and `distribution`: `redistributable`, `restricted` or `prohibited`.
  The operator must have the relevant rights. Declaring a license or writing an
  original summary does not establish permission to redistribute source text.
- One to 512 `requirements`, each with `id`, `title`, original `summary`,
  `category`, `applicability`, `evidencePrompt`, `objectiveBasis`, and explicit
  `objectives`. Objectives use `id`, `description`, `source`, `tool` and `ruleIds`.
  `objectivesComplete` defaults to false. `requiredEvidenceMethods` can name
  `deterministic_check`, `enquiry`, `evidence` or `observation`; optional
  `evidenceTypes` further describes needed material. Requirements can also retain
  their own `sources` and responsibility descriptions.

Only completed deterministic checks currently establish objective satisfaction.
Enquiry, document evidence, observations or unknown evidence types withhold
completeness and produce a diagnostic, even if all scanner checks pass. Rights
and independent assessor review remain Unverified when not independently evidenced.

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

## Reports and retention

The ordinary `controlAssessmentRef` and readable report retain pack identity,
revision, integrity, effective dates and intelligence digests. Redistributable
packs also retain the declared source, review, license, requirement and mapping
metadata. Restricted operator-supplied packs are retained behind the artifact
owner's existing access boundary; portable reports omit their titles, summaries,
source sections, rationales and objective descriptions, with an explicit omission
diagnostic. Prohibited packs are refused. Retention does not grant sharing rights
or permission to export the underlying licensed artifact.

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
objectives or rights is reported as an intelligence change. Both export tools
return an explicit unsupported result for a multi-pack run.

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

## ISO/IEC 27001 ISMS readiness

The contextual `security-iso-27001` preset and `framework.packId:
iso-27001-readiness` use the existing assessment/API and retained report path.
Ordinary Create remains Steps-first. Save `system_inputs` in an instance preset
when reusing an ISMS scope. The pack identifies ISO/IEC 27001:2022 and its published
Amd 1:2024 climate changes, using original readiness summaries and reference
identifiers rather than redistributing licensed standard text. Consult an
appropriately licensed copy for the complete normative requirements.

`systemInputs` records `systems`, `scopeStatement`, `context`, `interestedParties`,
`climateRelevance`, `risks`, `controls` and `requirementEvidence`. Management-system
clauses remain distinct from the 93 Annex A references. A risk has an `id`,
`version`, description, owner, assessment, treatment, `treatmentPlan`, `controlIds`
and `residualRiskApproval`; accepting a risk also needs `acceptanceReason`.
Missing treatment and approval records remain actions rather than inferred success.

Key controls by reference such as `A.8.8`; necessary additional controls use an
`EXTRA.` identifier and a title. Each decision retains `decision` (include,
exclude or undetermined), rationale, `riskIds`, owner and declared
`implementationStatus`. An exclusion without rationale, or of a control linked
to current risk treatment, stays unresolved. A scanner mapping is never needed
to retain a necessary control. Requirement choices may narrow systems and periods,
not expand them or exclude management-system requirements.

`requirementEvidence` maps clause/control IDs to the shared organisational evidence
requests described above. These preserve purpose-specific reviews and historical
revisions, but only current revisions are read, admitted and retained as evidence.
Internal audits, management reviews, training and corrective actions need records
of occurrence; a policy draft or management assertion does not prove they happened.
Management-clause evidence must match the clause’s documentary category; an
internal audit does not establish management review. Control evidence covers
design, adoption and occurrence separately. Missing, denied, expired,
out-of-population or wrong-purpose evidence remains a gap.
`evidenceRefs` can reuse scoped technical records for several requirement IDs,
without transferring one requirement's conclusion to another. Technical context
never replaces the organisational record review.

The structured framework artifact and primary report export the draft Statement
of Applicability with a content-derived version, inclusion/exclusion rationale,
implementation assertions, evidence coverage and risk links. Link a previous
framework artifact using `previousAssessmentRef`: changed risk, scope, period,
responsibility agreement or edition identifies affected controls and evidence for
review without editing the old artifact. The current report retains the prior
reference and SoA version.

Readiness describes recorded evidence only. Generated policies and SoAs remain
drafts, a passed vulnerability retest cannot close missing ISMS process evidence,
and actual organisational operation and certification remain **Unverified**.
