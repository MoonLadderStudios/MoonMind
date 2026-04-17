# Data Model: Create Page Repository Dropdown

## Repository Option

Represents one repository candidate safe to expose to the browser.

Fields:

- `value`: Required owner/repo repository identifier used as the submitted repository value.
- `label`: Required display label. Defaults to `value`.
- `source`: Required source classification: `default`, `configured`, or `github`.

Validation:

- `value` must match owner/repo syntax.
- Values containing URL credentials, tokens, query strings, fragments, or unsupported hosts are rejected.
- Duplicate values are collapsed case-insensitively while preserving the first source priority.

## Repository Options Result

Represents the Create page repository suggestion payload.

Fields:

- `items`: Ordered list of `RepositoryOption`.
- `error`: Optional non-secret warning string when credential-based discovery failed.

Validation:

- `items` may be empty.
- `error` must never include token-like values, secret refs, authorization headers, cookies, or raw GitHub response bodies.

## Create Page Draft Repository

Represents the repository value in the browser draft.

Fields:

- `repository`: Editable string. May be selected from repository options or manually typed.

Validation:

- Submit-time validation remains the existing owner/repo requirement.
- Selecting an option must update only this field.
