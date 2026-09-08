# Documentation assertion review — MoonLadderStudios/MoonMind#3964

**Document Class:** Imperative working document
**Status:** Implementation handoff; managed pytest execution blocked by infrastructure
**Issue:** MoonLadderStudios/MoonMind#3964
**Canonical Target:** Existing documentation, Skill, registry, and required-CI contracts
**Delete/Archive Trigger:** Archive after issue verification and publication retain this evidence.

This is a review record, not a new documentation schema or test-selection registry.
The trusted assessment was `PARTIALLY_IMPLEMENTED` at
`920461509bd77be63297f2913c847f2926b326b2`. Its unmet/partial requirements bound
this change. No runtime execution, credential, publication, or Skill instruction
semantics are changed.

## Assertion inventory

The `.md` candidate search found 148 Python test/helper files. The run artifact
`artifacts/docs-3964/assertion-inventory.json` records each baseline assertion,
its function and line, the surrounding function (including loop values), and
nonexclusive classifications. It is built from immutable Git source, not the
edited worktree. It is evidence, not a parser or list used to enforce behavior.
Mentioning Markdown in a synthetic payload does not make a test a prose check.

| Test surface | Actual assertion roles | Disposition |
| --- | --- | --- |
| `tests/unit/docs/test_documentation_architecture_standard.py` | Header fields, filenames, canonical claim IDs, issue IDs, authority/adoption rules, explanatory sentences | Preserve identifiers/examples; remove wording dependencies; authority/adoption replacements below |
| `tests/unit/docs/test_documentation_architecture_conformance.py` | Document class/status, absence of tracking/second authorities, bounded-context policy, optional historical evidence | Retain: these express authoring/authority policy, not cosmetic prose |
| `tests/unit/docs/test_viewpoint_templates.py` | Template existence, metadata fields/values, claim-heading examples, links, issue IDs; two descriptive labels | Retain contracts; remove the two header-description labels |
| `tests/unit/docs/test_final_docs_cleanup_policy.py` | Metadata, obsolete plan absence, secret/raw-evidence exclusion; historical manifest-consolidation sentence | Retain metadata/security/cleanup; retire historical completion wording |
| Former integration docs cleanup module | Runtime source and media-type tokens, superseded builder absence, plan cleanup, exact roadmap checkbox line | Move unchanged meaningful guards to `tests/unit/docs/test_final_docs_cleanup_contract.py`; preserve roadmap identifier independently of checkbox state |
| `tests/unit/docs/test_combined_stack_validation_docs.py` | Operator commands, rollback/destruction separation, workspace read-only mount, credential isolation, links | Retain every guard, including the OAuth-volume/global-home prohibitions |
| `tests/unit/docs/test_dood_phase0_contract.py` | Tool names, typed example flags, API/Temporal ownership, no agent Docker authority, workload neutrality, tombstones | Retain every guard; filename/phase naming is not evidence these contracts are obsolete |
| `tests/unit/docs/test_omnigent_create_to_host_contract.py` | JSON examples, wire identity, model validation, primary terminal status, host/selection authority | Preserve all semantics; loosen example-heading wording and add invalid-example cases |
| `tests/unit/docs/test_normal_codex_product_path_reconciliation.py` | Identity, no host/daemon authority or silent substitution, release-last order, support vocabulary, JSON | Retain every guard |
| `tests/unit/docs/test_omnigent_consolidation_3962.py` | Owner map, credential cleanup/support rules, owner links/anchors; two explanatory headings | Retain semantics, remove heading labels, use shared link validator |
| `tests/unit/docs/test_temporal_claims_reconciliation_3960.py` | SDK handlers/validators, registered activities/queues, authority disclosures, cross-links | Replace source regexes and copied registration lists with SDK/catalog owners; retain semantic document claims |
| `tests/unit/docs/test_workflow_execution_product_model_docs.py` | Product identity and allowed/forbidden terminology | Retain: changing these terms changes the product contract |
| `tests/unit/docs/test_status_authority_references.py` | No active references to archived status authority | Retain whole-repository reference guard |
| `tests/unit/docs/test_repository_access_slice0_reconciliation.py` | Proposed status, mandatory recovery gate, source/owner inventory, negative authority constraints, YAML fixture semantics | Retain; the active reconciliation record still governs work partition and security |
| `tests/unit/workflows/temporal/test_step_execution_conformance_gate_docs.py` | Executable commands, fixture triggers, degraded-input and compact-evidence policy; historical Phase 1 scope | Retain commands/security/degraded-input rules; retire historical rollout scope |
| Former `tests/integration/temporal/test_step_execution_contracts.py` | Source/document required and prohibited contract vocabulary | Move all assertions to the existing Temporal unit shard as `test_step_execution_contract_names.py` |
| `tests/unit/statuses/test_canonical_statuses.py` | Fenced/table status values compared with production enums; normalization and compatibility behavior | Retain; these are schemas and structured references |
| `tests/unit/workflows/temporal/test_workflow_catalog_generator.py` | Generated-reference bytes, real registrations/worker bindings, invalid-registration fixtures | Retain; exact bytes are meaningful for generated output (#3959) |
| `tests/unit/tools/test_check_documentation_architecture.py`, `test_check_documentation_links.py` | Production checker behavior, invalid synthetic docs, shipped validation doc | Retain and extend independent negative cases |
| `tests/unit/tools/test_index_moonspec_docs.py`, `test_slice_moonspec_docs.py` | Synthetic Markdown/JSON index and slice inputs, stable IDs, fenced examples, ref-only output | Retain; fixture wording is input/output data, not shipped prose |
| `tests/unit/agents/test_document_author_skill.py`, `test_document_health_review_skill.py`, `test_document_health_remediate_skill.py` | Frontmatter identity/capabilities/description plus portable instructions, output actions, authoring/security rules | Shared production metadata parser; description can be reworded; all instruction guards remain |
| Other `tests/unit/agents/*skill.py`, `test_moonspec_packet_provenance.py`, `test_moonspec_acceptance_assessment.py` | Portable Skill decisions, required order, provenance, output contracts and templates | Retain; instructions and packet schemas are executable contracts |
| `tests/unit/capabilities/test_batch_dependabot_resolver_skill.py`, `test_github_issue_verify_skill.py`, `test_input_contracts.py` | Shipped Skill inputs, readiness, output and verifier rules | Retain production-schema and instruction checks |
| Shipped-Skill cases in `tests/unit/services/test_skill_resolution.py` and `tests/unit/test_pr_resolver_tools.py` | Exact capabilities, immutable portable authority, fresh provider evidence, remote completion; other cases use generated fixtures | Retain; these guard real host and authority boundaries |
| `tests/unit/test_codex_conformance_workflow.py` | Executable canary path, evidence destination and external-mutation prohibition | Retain; `.md` is an executable provider fixture |
| `tests/unit/specs/*.py` | Optional run-local packet traceability, requirement IDs; copied brief headings/titles | Preserve IDs, packet structure and source coverage; remove copied title/label dependencies in MM-649/655/656/680 |
| Other candidates (runtime, API, RAG, manifest, provider, helpers, tooling) | Synthetic Markdown paths/content, bundled bytes, output refs, source comments, generated files and runtime assertions | Preserve. For example the fake Omnigent `README.md` and immutable Skill materialization sentinels test data transport, not narrative docs |

## Removed or replaced meaningful guards

The named regressions are committed tests; infrastructure prevented a fresh
pytest result in this run. Existing tests listed as retained evidence were
inspected, not claimed as newly passing.

| Original assertion | Owning replacement / justification | Mutation or regression evidence |
| --- | --- | --- |
| Filename alone/declared authority and parallel authorities, exact sentences | Existing `check_documentation_architecture` declaration and duplicate-authority checks | `test_authority_comes_from_declaration_regardless_of_filename` adds a second declared system view under Overview/System/Design filenames; existing missing-authority and duplicate-title negatives remain |
| Incremental/advisory adoption and malformed/duplicate-ID wording | Existing checker CLI default `changed`, structured findings, advisory return code | `test_incremental_default_reports_changed_violations_without_retroactive_churn` covers omitted/explicit scope with old missing metadata and new malformed claims; existing duplicate-ID and strict-mode negatives remain |
| Copied canonical prefix list and wording distinguishing design traceability | `CANONICAL_CLAIM_PREFIXES` and the checker remain the authority; shipped examples still name the prefixes | Existing `test_design_req_traceability_is_not_a_canonical_claim_id`, malformed-ID and duplicate-ID fixtures do not derive their invalid inputs from the implementation |
| Three local/text Skill frontmatter checks | `_load_skill_frontmatter` and `extract_required_capabilities_from_skill_markdown`; still require the exact canonical name, nonempty description, and `git` capability | `test_document_skill_metadata_regressions.py`: missing/unclosed/invalid YAML, wrong name, empty description, scalar capabilities, missing git; valid description rewording and YAML quoting |
| Description wording enumerating document actions | Presentation copy is no longer duplicated in metadata tests; the action/decision instructions remain guarded in their original tests | Remediate supported-action tests, review disposition tests, author architecture-field tests retained; negative authoring-prohibition mutation proves a changed instruction still fails |
| Omnigent local slug/link parser | `tools.check_documentation_links.run_checks` on the real doc set | `test_shipped_link_guard_detects_mutated_target`: missing target, cross-document anchor, same-document anchor. Shared negative fixtures additionally cover images/reference-style links. The old local counter was a parser-execution sanity check; injected failures now prove the actual guard visits the documents |
| Exact request-example heading/spacing | Stable section number identifies JSON; `AgentExecutionRequest` still validates the shipped example | Reworded heading passes; invalid kind, missing agent and malformed JSON reject independently |
| Source regexes for Pause/Resume, control_state and update validators | Temporal SDK definitions on the real workflow classes | `test_sdk_contract_checks_detect_registration_violations`: deleted Update, extra Query, missing validator |
| Copied Skill operation and fleet-helper lists/count; AST queue extraction | Production catalog binding validation, SDK Activity names and client queue tuple, compared to independently authored docs | `test_registry_document_checks_detect_omitted_references` deletes an operation, handler or queue from the document; existing catalog-generator added/duplicate/unsupported-registration tests retained |
| Exact unchecked roadmap line | Durable `5.4 Resume-from-checkpoint default flow` identifier retained; task progress state is not identity | `test_resume_identifier_guard_ignores_progress_but_detects_removal`: checking the box passes, deleting the identifier fails |

Other removed assertions have no current executable dependency: descriptive
header labels, the word “preferred,” explanations beside preserved filename
examples, copied issue titles, and descriptions beside preserved Skill
instructions. The old generic `Architecture.md`/`Overview.md` sentence was a
superseded recommendation; the preferred module paths remain asserted.
The global `contracts/` policy token is retained because the advisory checker
does not enforce that separate authoring constraint.

The Phase 1 / PR #2454 / stale-WP1 scope and manifest-consolidation-completed
sentence describe completed rollout work. Pinning them would require keeping
historical scaffolding in canonical docs forever. They are obsolete as test
contracts. The real manifest writer/media-type and superseded-builder absence
guards, conformance commands, fixture triggers, forbidden inline evidence,
degraded input rules and production manifest/conformance tests remain. Minor
downstream-authoring-adjustment wording is guidance, not a product-enforced
constraint; document classes and authoring rules remain independently checked.

## CI ownership and validation handoff

`tools/select_test_suites.py` selects `unit_fast` for documentation changes,
including assets that may be link targets. Markdown outside `docs/` also reaches
unit checks. Existing fail-open and Skill selection behavior remains.
The conformance guidance documents additionally select `temporal_boundary`;
the registry-generated-reference routing from #3959 remains intact.

Both former integration modules only read files. They now use the existing
unit-fast or Temporal shard; no new marker, shard, CI job or semantic registry
was introduced. Their ownership tests inspect actual pytest node markers and
use the production shard verifier and impact selector. Selector regression
tests cover ordinary docs, metadata templates, assets, README/AGENTS, temporary
docs and Temporal contracts. The roadmap reference follows the moved file.

The selector itself is a fail-open/full-backend input. Full required CI remains
necessary before publication; this step does not claim that gate passed.

Two attempts through `moonmind container python-tests` failed before pytest
started with `failureClass=infrastructure`: “container ownership could not be
read from the container backend.” Authoritative evidence:

- `container-job:a9e1e0c1d6464833a2758c686ecbb784`, logs `art_01M1Z6EQ0P1W6ZVMA68RPPCQ63`, artifacts `art_01M1Z6EQKPGA3V9P0GRBQMKJNJ`.
- `container-job:575bc59864fe49909053fdef1e0253bf`, logs `art_01M1Z6RH3GZ97SB7Y3QBPSA689`, artifacts `art_01M1Z6RH91S17V9QMVX875PR8A`.

The standalone shared link-check CLI completed over `docs/Omnigent/*.md` with
zero findings. Output is `artifacts/docs-3964/omnigent-links.json`. This is
static-check evidence, not a pytest result. No direct Docker or local pytest
fallback was used.

Python syntax parsing passed for all 20 changed/new Python files and
`git diff --check` passed. Read-only production selector probes confirmed the
ordinary-doc and Temporal-document routing; exact outputs are recorded in
`artifacts/docs-3964/selection-evidence.json`.

After the backend is repaired, run the changed unit suites and moved vocabulary
test, plus retained link/metadata/resolver/catalog/conformance/native-authority
regressions. The full selector-required gates and publication are owned by the
later verification/publication stages.
