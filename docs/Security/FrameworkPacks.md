# Declared framework packs

`security.assess` accepts an operator-supplied framework pack through the same
artifact admission, Skill invocation and report path as native security evidence.
There is no registry, scheduler or source download. The three bundled gaming
preparation packs still use `framework`; declared packs use `frameworkPack` and
the existing requirement evaluator.

## Selecting a pack

Supply `frameworkPack` with `artifactRef`, `packId`, `edition`, `revision`, the
artifact's `sha256`, and an assessment `period` (`start`, `end`). Optional
`requirementChoices` is keyed by requirement ID. Each choice can narrow `systems`
and `period`, and records `applicability` plus `applicabilityReason`. Omitted
applicability remains undetermined. Explicit applicable/not-applicable choices
need a rationale; `notApplicableReason` is also accepted. Choices cannot expand
admitted assets or drop exclusions and repository exceptions.

`previousAssessmentRef` optionally selects a retained control-assessment report
for comparison. Do not also supply `controlAssessment`: the selected pack compiles
the sole requirement plan. Native evidence tools or an explicit review are still
required. A pack and its crosswalks are not evidence that checks ran.

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
- One to 64 `requirements`, each with `id`, `title`, original `summary`,
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

A control report seals the selected pack alongside its actual native evidence.
Retries recheck access and integrity, then reuse native work when unchanged.
Edition, mapping, objective, provenance and rights changes appear as assessment
intelligence changes, never a newly remediated system. A missing, foreign or
unsealed previous report makes that comparison Unverified; it does not rewrite
history or discard usable current evidence. Changed applicability and methods are
reevaluated rather than inheriting a prior green conclusion.

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
