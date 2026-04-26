# Data Model: Typed Deployment Update Tool Contract

## Deployment Update Tool Definition

- `name`: `deployment.update_compose_stack`
- `version`: `1.0.0`
- `type`: `skill`
- `description`: Operator-readable summary of the privileged Compose stack update operation.
- `inputs.schema`: Strict JSON schema for bounded deployment update inputs.
- `outputs.schema`: JSON schema for structured result and artifact references.
- `executor.activity_type`: `mm.tool.execute`
- `executor.selector.mode`: `by_capability`
- `requirements.capabilities`: `deployment_control`, `docker_admin`
- `policies.timeouts`: start-to-close and schedule-to-close bounds for privileged update execution.
- `policies.retries`: `max_attempts = 1` and non-retryable privileged failure codes.
- `security.allowed_roles`: `admin`

## Deployment Update Plan Node Inputs

- `stack`: allowlisted stack name. V1 enum: `moonmind`.
- `image.repository`: allowlisted MoonMind image repository.
- `image.reference`: requested tag or digest reference.
- `image.resolvedDigest`: optional resolved digest.
- `mode`: `changed_services` or `force_recreate`.
- `removeOrphans`, `wait`, `runSmokeCheck`, `pauseWork`, `pruneOldImages`: bounded booleans.
- `reason`: required operator reason.

## Validation Rules

- Root input object rejects unknown fields.
- `image` object rejects unknown fields.
- Required fields are `stack`, `image`, and `reason`; `image.repository` and `image.reference` are required inside `image`.
- Unsupported mode values fail schema validation.
- Shell/path/runner override fields are not part of the schema and fail before execution.
