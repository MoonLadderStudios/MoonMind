# Task Presets System

Status: Active  
Owners: MoonMind Engineering (Task Platform + UI)  
Last Updated: 2026-03-13  

## 1. Purpose

Define the MoonMind "Task Presets" system: a server-hosted catalog of step templates with compile-time expansion into concrete `task.steps[]`. The design keeps the execution contract unchanged while giving UI, CLI, and MCP users reusable orchestrations, convenient editing affordances, and the ability to save real task steps back into the catalog.

## 2. Goals and Non-Goals

### Goals

- Provide a single authoritative task step template catalog with versioning, ownership, and scopes (global/team/personal).
- Offer deterministic server-side expansion, validation, and audit tracking before tasks are executed.
- Deliver UI conveniences (preview, append/replace, collapse-as-group, favorites) without changing the task payload schema.
- Support CLI/MCP flows via REST endpoints identical to the UI.

### Non-Goals

- Changing Temporal Workflow execution behavior or allowing parameter substitutions during runtime (remains an anti-pattern).
- Replacing AgentKit skills or orchestrator workflows (templates are complementary UI conveniences).

## 3. System Overview

```
             +---------------------+
             | Template Catalog DB |
             +----------+----------+
                        ^
                        | CRUD + version seed
+-------------+   REST  |                      +----------------+
| Task UI /   +-------->+  Task Template API  <-+ MCP / CLI / CI |
| Automations |         |                      +----------------+
+------+------+         v
       |          +-----+------------------+
       | expand   | Step Expansion Service |
       +--------->+ (validation + hydrate) |
                  +-----+------------------+
                        v
                  +-----+------------------+
                  | Task Payload Compiler |
                  | merges steps + audit  |
                  +-----+------------------+
                        v
                  +-----+------------------+
                  | Temporal Run Execution |
                  +------------------------+
```

Key properties:

- Templates are stored centrally and exposed via FastAPI routers under `/api/task-step-templates`.
- The expansion service applies inputs, generates stable step IDs, validates schema compliance, and emits derived metadata.
- The compiler merges expanded steps into the task payload, updates `task.appliedStepTemplates`, and submits it to the backend.

## 4. Template Model

Templates define `.yaml` configuration containing variables (`inputs_schema`) and a sequence of tasks/prompts (`steps`). They do not bypass authorization policies. When executed, they expand into an explicit array of instructions sent to the `MoonMind.Run` Temporal workflow.
