# Orchestrator Use Cases in the Temporal Era

This document outlines the core use cases for the MoonMind Orchestrator (`mm-orchestrator`) that remain highly relevant as the platform migrates to a Temporal-backed execution architecture.

While the underlying execution engine is shifting from legacy queue-backed models to Temporal Workflows, the Orchestrator's core mission—safely modifying code, managing Docker environments, and ensuring resilient execution—remains unchanged.

## 1. Automated Code Remediation & Patching
The Orchestrator interprets high-level instructions (e.g., "add missing dependency and fix build for service X") and safely modifies code and configuration files within the repository.
*   **Dependency Management:** Automatically injects missing system packages into `Dockerfiles` or adds required Python/Node packages to dependency files (e.g., `requirements.txt`, `package.json`).
*   **Policy Enforcement:** Edits are restricted to allow-listed files within specific service directories to prevent unauthorized modifications to core infrastructure or secrets.

## 2. Environment Lifecycle Management (Build & Restart)
Operating via "Docker-outside-of-Docker" (DooD) on a single host, the Orchestrator directly interfaces with the `docker-compose` control plane.
*   **Targeted Rebuilds:** Executes isolated builds for modified services (`docker compose build <service>`) without disrupting the broader stack.
*   **Seamless Relaunch:** Restarts specific services using updated images (`docker compose up -d --no-deps <service>`), acting as a local CD pipeline for the agent's changes.

## 3. Automated Health Verification & Rollback
After deploying changes, the Orchestrator ensures the environment remains stable through multi-tiered verification.
*   **Health Checks:** Automatically verifies container execution state and polls HTTP `/health` endpoints with exponential backoff.
*   **Fail-Safe Rollback:** If verification fails, the Orchestrator automatically reverts the environment. This includes reverting Git patches, rebuilding the previous image state, and restarting the service to ensure zero downtime from broken AI patches.

## 4. Human-in-the-Loop Approvals
For critical or production-sensitive services, the Orchestrator integrates natively with Temporal Signals to pause execution and request human authorization.
*   **Approval Gates:** Workflows transition into an `awaiting_approval` state.
*   **Operator Review:** SREs or operators can review the proposed ActionPlan (diffs, target services) in Mission Control before explicitly granting approval to proceed with the build and relaunch phases.

## 5. Unified Skill Authoring
In the Temporal era, Orchestrator Tasks are aligned with standard Agent workflows.
*   **Skill Composition:** Orchestrator workflows can be authored using the same step-and-skill paradigm as standard agent tasks.
*   **Dynamic Sequences:** While default workflows follow a standard `analyze -> patch -> build -> restart -> verify` loop, operators can define custom sequential skill executions via the unified submit form.

## 6. Resilient Execution During Database Outages
By migrating from a fragile dual-write Postgres/Agent Queue model to Temporal, the Orchestrator gains native resilience.
*   **Degraded Mode Operation:** If the primary MoonMind Postgres database goes offline, the Temporal-backed Orchestrator Worker continues to lease activities, execute Docker builds, and stream logs.
*   **Durable State:** Progress is durably checkpointed in Temporal's workflow history rather than relying on custom database updates, ensuring runs can reliably resume or rollback even during partial infrastructure failures.
