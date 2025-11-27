# Data Model: Scalable Codex Worker

**Feature**: Scalable Codex Worker (007)
**Date**: 2025-11-27

## Conceptual Entities

### Infrastructure Components

#### Codex Worker Service
- **Type**: Service (Container)
- **Role**: Executes tasks from the `codex` queue.
- **Cardinality**: 1..N (Scalable)
- **State**: Stateless (except for mounted volume).
- **Configuration**:
  - `CELERY_QUEUES`: `['codex']`
  - `CODEX_HOME`: `/home/app/.codex` (Mounted Volume)

#### Codex Auth Volume
- **Type**: Persistent Storage (Docker Volume)
- **Role**: Stores authentication state.
- **Contents**:
  - `credentials`: OAuth tokens.
  - `config.toml`: CLI configuration (`approval_policy = "never"`).
- **Cardinality**: 1 (Shared across all worker replicas).

#### Codex Queue
- **Type**: Message Queue (Redis/RabbitMQ)
- **Role**: Buffers tasks destined for Codex processing.
- **Task Types**: `submit_to_codex`, `poll_codex`, `apply_diff`.

## Configuration Schemas

### Docker Compose Service Definition (Draft)

```yaml
services:
  celery_codex_worker:
    image: moonmind-api-service:latest
    command: /bin/sh -c "codex whoami && celery -A celery_worker.speckit_worker worker -l info -Q codex"
    volumes:
      - codex_auth_volume:/home/app/.codex
    depends_on:
      - redis
      - api_service
    deploy:
      replicas: 1
```

### File System Layout (Inside Volume)

```text
/home/app/.codex/
├── credentials      # JSON/Binary OAuth token store
└── config.toml      # CLI Configuration
```
