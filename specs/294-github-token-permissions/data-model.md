# Data Model: GitHub Token Permission Improvements

## GitHub Credential Source

Represents the non-secret source category selected by canonical credential resolution.

Fields:
- `source_kind`: one of `explicit`, `direct_env`, `secret_ref_env`, `settings_token_ref`.
- `source_name`: redaction-safe source identifier such as `GITHUB_TOKEN`, `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`, `GITHUB_TOKEN_SECRET_REF`, `WORKFLOW_GITHUB_TOKEN_SECRET_REF`, or `MOONMIND_GITHUB_TOKEN_REF`.
- `repo`: optional `owner/repo` target used for diagnostics.
- `resolved`: boolean indicating whether a usable token was found.
- `diagnostic`: optional redaction-safe diagnostic for missing or unresolvable credentials.

Validation rules:
- Raw token values must never be stored on this entity.
- Secret references may be shown only as allowed reference identifiers.
- Exactly one winning source is reported when resolution succeeds.

## GitHub Permission Profile

Defines the required and optional repository permissions for one MoonMind operation mode.

Fields:
- `profile_id`: stable identifier such as `indexing`, `publish_pr`, or `readiness`.
- `repository_access`: selected repository requirement.
- `required_permissions`: list of permission name and level pairs.
- `optional_permissions`: list of permission name, level, and reason pairs.
- `applies_when`: conditions such as workflow-file modification or reaction fallback enabled.

Validation rules:
- Indexing requires contents read.
- Publishing requires contents write and pull requests write.
- Workflow-file modification requires workflow write.
- Readiness requires pull request read, commit status read, checks read, and issue read when reaction fallback is enabled.

## GitHub Permission Diagnostic

Sanitized provider failure detail for GitHub authorization and permission errors.

Fields:
- `operation`: operation name such as `create_pull_request`, `merge_pull_request`, `readiness_checks`, or `token_probe`.
- `http_status`: numeric provider status when available.
- `message`: sanitized GitHub response message when available.
- `documentation_url`: sanitized provider documentation URL when available.
- `accepted_permissions`: parsed or raw sanitized value from `X-Accepted-GitHub-Permissions` when available.
- `required_permission`: optional MoonMind-derived permission remediation.
- `retryable`: boolean.

Validation rules:
- Raw token-like values must be redacted from all text fields.
- Provider body content outside known safe fields must not be surfaced verbatim.
- A 403 with accepted permissions should map to a specific remediation when possible.

## Token Probe Result

Repository-specific validation output for a resolved credential.

Fields:
- `repo`: target `owner/repo`.
- `mode`: one of `indexing`, `publish`, `readiness`, or `full_pr_automation`.
- `credential_source`: `GitHub Credential Source`.
- `repository_accessible`: boolean or unknown.
- `default_branch_accessible`: boolean or unknown.
- `pull_request_accessible`: boolean or unknown.
- `permission_checklist`: list of required and optional permission results.
- `diagnostics`: list of `GitHub Permission Diagnostic`.
- `limitations`: list of known external limitations that may apply.

Validation rules:
- Probe targets exactly one selected repository.
- Probe must not use global repository listing or classic OAuth scope assumptions.
- Probe output must be safe to show in Mission Control and artifacts.

## Readiness Evidence Availability

Per-evidence state in PR readiness evaluation.

Fields:
- `evidence_source`: `pull_request`, `commit_status`, `checks`, `reviews`, or `issue_reactions`.
- `availability`: `available`, `unavailable_optional_permission`, `failed_required`, or `not_configured`.
- `missing_permission`: optional permission name.
- `summary`: redaction-safe operator note.
- `retryable`: boolean.

Validation rules:
- Required pull request state failures remain blockers.
- Optional evidence permission failures become unavailable evidence notes when policy allows.
- Unavailable optional evidence must not mask other failing required evidence.

## State Transitions

Credential resolution:
1. `unresolved` -> `resolved` when a winning source yields a token.
2. `unresolved` -> `missing` when no source is present.
3. `unresolved` -> `unresolvable_reference` when a configured reference cannot resolve.

Token probe:
1. `not_started` -> `repository_checked`.
2. `repository_checked` -> `mode_checked`.
3. `mode_checked` -> `passed`, `failed_missing_permission`, or `inconclusive_external_limitation`.

Readiness evidence:
1. `not_checked` -> `available` when provider evidence is fetched.
2. `not_checked` -> `unavailable_optional_permission` when optional permission is missing.
3. `not_checked` -> `failed_required` when required provider state cannot be fetched.
