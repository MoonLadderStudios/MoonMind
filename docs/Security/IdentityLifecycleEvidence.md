# Identity lifecycle evidence

`security.assess` supports a bounded, read-only Okta integration and an uploaded
workforce-event artifact. It collects evidence for organisational review and
sampling. It does not decide compliance, administer accounts, or send messages.

## Provider and access

The initial provider is Okta on an explicit organisation domain under `okta.com`,
`okta-emea.com`, or `oktapreview.com`. Custom domains and additional providers
require a separate extension. Supply a scoped OAuth access token through an
active managed [Secret](SecretsSystem.md), referenced as `db://okta-reader`.
Token issuance and refresh remain with the operator's provider integration;
expired or denied access stays visible as unavailable evidence.

| GET endpoint | OAuth scope | Evidence |
| --- | --- | --- |
| `/api/v1/users` | `okta.users.read` | Account IDs and current status |
| `/api/v1/users` with `filter=status eq "DEPROVISIONED"` | `okta.users.read` | Deprovisioned accounts omitted by the default list |
| `/api/v1/users/{id}/groups` | `okta.users.read` | Group IDs |
| `/api/v1/users/{id}/roles` | `okta.roles.read` | Assigned administrative role IDs |
| `/api/v1/logs` with bounded `since`, `until`, and ascending order | `okta.logs.read` | Relevant lifecycle and membership events |

These permissions are documented by Okta for [users](https://developer.okta.com/docs/api/openapi/okta-management/management/tags/user/other/listusers),
[user groups](https://developer.okta.com/docs/api/openapi/okta-management/management/tags/userresources/other/listusergroups),
[role assignments](https://developer.okta.com/docs/api/openapi/okta-management/management/tags/roleassignmentauser/other/listassignedrolesforuser),
and the [System Log](https://developer.okta.com/docs/api/openapi/okta-management/management/tags/systemlog).
The provider also limits visibility through the token's administrative assignments.
The reader never follows redirects or accepts arbitrary endpoints. Pagination
may change only the cursor within the original endpoint and query.

The Activity worker must use the existing scoped outbound proxy. Its
`MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES` declaration must contain the selected
policy reference, the exact workflow ID, and an HTTPS `provider-api` destination
for this organisation on port 443. `MOONMIND_EGRESS_PROXY` selects that admitted
proxy using the existing HTTP adapter contract. An absent policy or proxy fails
before credential delivery; there is no direct-network fallback. Configure and
operate the proxy through the existing scoped-egress deployment owner.

## Admitted inputs

Upload a `workforce-events/v1` JSON document through the existing artifact import
path. Unsupported HR sources use this format; there is no additional HR connector.
The organisation and timezone-aware period must match the collection request.
Use only the necessary source identifiers:

```json
{
  "schemaVersion": "workforce-events/v1",
  "organisation": "example.okta.com",
  "since": "2026-11-01T00:00:00Z",
  "until": "2026-11-06T00:00:00Z",
  "roster": [
    {"personId": "person-1", "accountId": "00uExample", "accountKind": "human"},
    {"accountId": "00uService", "accountKind": "service"}
  ],
  "events": [
    {"id": "departure-1", "personId": "person-1", "type": "leaver", "effectiveAt": "2026-11-02T00:00:00Z"}
  ]
}
```

Event types are `joiner`, `mover`, and `leaver`. Movers declare the old access to
remove in `removeGroupIds`. Set `effectiveAt` to `null` when unknown. Missing
account IDs, duplicate or conflicting identities, unspecified mover changes, and
unmatched people or accounts require review. Names and email similarity never
establish a match. Service identities need an explicit roster classification;
an unmatched account is not automatically improper. Extra HR attributes are
discarded during parsing. Avoid including them in the original upload, whose
access and retention remain owned by the artifact service.

Select the tool through its existing Temporal Activity handoff:

```json
{
  "scope": {"assets": ["example.okta.com"]},
  "tools": ["identity-lifecycle"],
  "credentialRefs": ["db://okta-reader"],
  "networkRefs": ["identity-read"],
  "budget": {"baseSeconds": 120, "maxSeconds": 180, "progressStallSeconds": 60},
  "identityLifecycle": {
    "provider": "okta",
    "organisation": "example.okta.com",
    "since": "2026-11-01T00:00:00Z",
    "until": "2026-11-06T00:00:00Z",
    "workforceRef": "art_uploaded_workforce",
    "secretRef": "db://okta-reader",
    "egressPolicyRef": "identity-read",
    "removalHours": 24,
    "privilegedGroupIds": ["00gPrivileged"],
    "requirementIds": ["5.16", "5.18", "6.5"]
  }
}
```

`removalHours` is required. The request defaults to 60 seconds, 128 requests,
16 pages per endpoint, 10,000 received items, and 8 MiB of response data. Optional
`maxSeconds`, `maxRequests`, `maxPages`, `maxItems`, and `maxBytes` tighten or raise
these within the schema's bounds. Collection must fit the assessment's base
budget. Larger organisations can produce incomplete assignment coverage; the
reader reports that limit rather than inventing empty membership or privilege.

## Coverage, retention, and review

The restricted report retains endpoint outcomes, pagination, requested period,
collection time, capability and retention limits, source event IDs, and artifact
links. Account and assignment results are current snapshots. Audit timestamps
are event publication times, not independently verified effective removal times.
A successful recorded removal after `removalHours` is a traceable policy
exception; missing events or unknown workforce dates remain review questions.
Reactivation or restored group access after removal also requires attention.

Okta documents a [90-day System Log retention limit](https://developer.okta.com/docs/reference/system-log-query/).
The report preserves an estimate based on the original collection time and
explicitly marks older requested periods. Complete pagination describes the
bounded API read, not historical completeness or downstream application access.
Hard-deleted accounts, downstream entitlements, and effective removal remain
unverified. An empty or disabled-account snapshot cannot establish historical
control operation.

The existing artifact owner saves projected pages before reconciliation. An
Activity retry or restart reuses completed pages and the original collection
window and budget. A completed partial report stays partial on retry; a new
collection needs a new execution. Restricted evidence is sealed to the workforce
input and saved pages. Provider profiles, names, email addresses, actors, raw
error bodies, and credential values are omitted from collected records.
Ordinary outputs contain counts, limitation codes, and evidence references.

When a framework is selected in the same assessment, the generated
`technical_assessment` evidence enters the existing framework preparation path
with the requested `requirementIds`. It supplies review and sampling inputs,
and cannot satisfy a control on its own. Review the matching and limitations,
sample lifecycle cases including privileged and service accounts, and retain
the existing enquiry, evidence, and observation records needed by that review.
Live-provider access and organisation-specific matching accuracy require an
authorised provider environment and workforce ground truth; controlled transport
tests do not establish those claims.
