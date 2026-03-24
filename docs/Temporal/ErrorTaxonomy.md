# Temporal Error Taxonomy

This document maps `ApplicationError` subtypes and other exceptions to their classification as retryable or non-retryable in MoonMind.

## Retryable Errors
* Transient network errors
* Rate limits (`429` / `PROVIDER_RATE_LIMIT_ERROR_CODE`) - usually handled via cooldowns.

## Non-Retryable Errors
* `INVALID_INPUT`: The input payload, parameters, or artifacts are malformed and will never succeed without modification.
* `ProfileResolutionError`: When no enabled auth profiles are found for the requested runtime ID.
* `UnsupportedStatus`: Integration reported a status that the workflow doesn't know how to handle.

## Notes on Temporal Python SDK Limitations
* **Signal-With-Start (`signal_with_start`)**: The current version of `temporalio` does not provide a `signal_with_start` method on `workflow.ExternalWorkflowHandle` objects (for use inside workflows). Thus, the `try-catch` pattern using `auth_profile.ensure_manager` activity in `MoonMind.AgentRun`'s `_ensure_manager_and_signal` method cannot be easily replaced with `signal_with_start` directly from the workflow context.
