# MM-1063 recorded trusted-tool lifecycle evidence

These fixtures are sanitized recorded trusted-tool evidence for MM-1063 lifecycle
validation. They intentionally contain no credentials, cookies, authorization
headers, or provider-native raw response dumps.

The recorded evidence covers:

- GitHub issue creation from a provider-neutral story handoff.
- GitHub downstream workflow handoff creation.
- GitHub issue status update gated by a `FULLY_IMPLEMENTED` verifier artifact.
- Jira lifecycle transition gated by a `FULLY_IMPLEMENTED` verifier artifact.
- Pull request publication blocked for non-`FULLY_IMPLEMENTED` verifier verdicts.

Residual live-provider risk: these fixtures prove the trusted-tool request and
decision boundaries using sanitized recorded inputs and outputs. They do not
prove current GitHub or Jira API availability, token scope validity, project
workflow configuration, or organization-specific label/status policies at test
time. Live provider verification remains optional/manual when credentials are
available.
