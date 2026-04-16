# Research: OAuth Terminal Docker Verification

## Input Classification

Decision: Treat `MM-363` as a single-story runtime feature request.

Rationale: The preset brief contains one user story focused on producing Docker-backed integration evidence for OAuthTerminal managed-session auth behavior. It references `docs/ManagedAgents/OAuthTerminal.md` as runtime source requirements, and the user explicitly selected runtime mode.

Alternatives considered: A broad design breakdown was rejected because the brief already names one independently testable story. Documentation-only mode was rejected because the request requires runtime verification evidence.

## Docker Evidence Source

Decision: Use `./tools/test_integration.sh` as the required integration command and treat Docker socket absence as a blocking environment condition.

Rationale: Repo instructions define this script as the hermetic integration runner for `integration_ci`; prior verification reports identify lack of `/var/run/docker.sock` as the remaining blocker. MM-363 explicitly asks for Docker-enabled integration evidence.

Alternatives considered: Unit-only verification and fake-runner integration evidence were rejected because the story exists specifically to close gaps left by those weaker evidence classes.

## Report Closure Policy

Decision: Update prior verification reports from ADDITIONAL_WORK_NEEDED only after passing Docker-backed evidence exists; otherwise record the exact blocker.

Rationale: The Jira brief makes false closure a requirement violation. This also aligns with the constitution's verification and continuous-improvement principles.

Alternatives considered: Marking reports complete based on existing unit evidence was rejected because it contradicts the original request and current report gaps.

## Secret Handling

Decision: Summarize Docker and integration output and redact any credential-like content before writing reports.

Rationale: Source design section 8 and security guardrails prohibit credential contents in workflow payloads, artifacts, logs, UI responses, and published summaries.

Alternatives considered: Copying full compose output into verification reports was rejected because it can expose environment details and is explicitly discouraged by repo security guardrails.
