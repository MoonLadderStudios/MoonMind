# Research: Create Page Repository Dropdown

## Repository Option Source

Decision: Build repository options from the workflow default repository, comma-delimited `GITHUB_REPOS`, and best-effort GitHub API repository listing when credentials can be resolved.

Rationale: The Jira brief asks for repositories the operator has added plus repositories available through credentials. Existing settings already expose `workflow.github_repository`, `github.github_repos`, and GitHub token resolution helpers, so the feature can reuse configured state without new storage.

Alternatives considered: A new database-backed repository registry was rejected because the story does not require persistence. A closed select fed only by settings was rejected because it would not satisfy credential-visible repository discovery.

## Browser Contract

Decision: Keep the repository control as an editable text input with datalist suggestions.

Rationale: The current Create page accepts manually typed owner/repo values and validates them on submit. A datalist preserves manual entry while offering selectable options, matching the graceful-degradation requirement.

Alternatives considered: A closed dropdown was rejected because it would block repositories that cannot be discovered. A separate modal picker was rejected as unnecessary for the single-story scope.

## Secret Boundary

Decision: Expose only owner/repo option values, labels, source metadata, and non-secret warnings in runtime config.

Rationale: The Create page must remain browser-safe. Tokens, secret refs, authorization headers, cookies, and credential-bearing clone URLs must never enter the boot payload.

Alternatives considered: Passing credential status details to the browser was rejected because it risks leaking operator configuration. Surfacing raw clone URLs was rejected because configured URLs may contain credentials or unnecessary host data.

## Testing Strategy

Decision: Cover normalization/discovery in Python unit tests and Create page option rendering/submission in Vitest.

Rationale: Repository option normalization and credential fallback are backend behavior, while datalist rendering and submit payload behavior are frontend behavior. Existing test suites already cover both boundaries.

Alternatives considered: Compose-backed integration tests were rejected because this story does not add persistent infrastructure or service topology.
