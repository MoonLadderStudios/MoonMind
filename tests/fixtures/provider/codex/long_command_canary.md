# Codex Long-Command Canary Fixture

Issue: MoonLadderStudios/MoonMind#3150

Run a harmless foreground shell command that outlives the first tool yield,
poll the same process at least once after the yield, and write
`var/conformance/long_command_result.json` only after the helper process exits.

The marker JSON must include:

- `schemaVersion`
- `scenarioVersion`
- `nonce`
- `command`
- `processExitCode`
- `startedAt`
- `completedAt`
- `durationSeconds`
- `outputSha256`

Do not mutate GitHub, create a pull request, or write outside the isolated
canary workspace except for the marker.
