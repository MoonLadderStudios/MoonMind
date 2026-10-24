# Security

## Reporting a vulnerability

Report suspected vulnerabilities privately through [GitHub private vulnerability reporting](https://github.com/MoonLadderStudios/MoonMind/security/advisories). Choose **Report a vulnerability** rather than opening a public issue with exploit details or credentials.

Include the affected version or commit, deployment configuration with secrets removed, impact, and the smallest reproducible example. Do not include live passwords, provider tokens, personal data, or unredacted logs. Coordinate public disclosure with the maintainers after they have assessed the report.

## Deployment boundaries

MoonMind is self-hosted and intended for one operator per instance. Keep the default loopback binding for local use. Before exposing it to another machine, configure authentication and ingress controls as described in [Authentication Contracts](docs/Security/AuthenticationContracts.md).

Container isolation is not a blanket security guarantee. Restricted network access needs an enforced configuration; see [Restricted Egress](docs/Security/RestrictedEgress.md). Review [Secrets System](docs/Security/SecretsSystem.md) for credential handling and the limits of opt-in outbound scanning.
