# Tasks: Gemini 429 Cooldown Retry

## 1. Runtime Detection

- [ ] 1.1 Add Gemini live-output parsing for capacity-exhausted 429 markers.
- [ ] 1.2 Extend runtime streaming/supervision so Gemini 429 detection can terminate the process early.
- [ ] 1.3 Ensure managed Gemini result enrichment maps those failures to provider error code `429`.

## 2. Workflow Cooldown + Timeline

- [ ] 2.1 Cache provider profile cooldown configuration in `MoonMind.AgentRun`.
- [ ] 2.2 Replace the hardcoded managed 429 cooldown with the configured value.
- [ ] 2.3 Emit a retry-specific `awaiting_slot` summary with scheduled retry timing so task details show the reason.

## 3. Defaults and Validation

- [ ] 3.1 Update new provider profile defaults to 900-second cooldown.
- [ ] 3.2 Add/update unit and integration tests for supervisor detection and workflow retry behavior.
- [ ] 3.3 Run `./tools/test_unit.sh` and fix regressions.
