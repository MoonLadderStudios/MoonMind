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
