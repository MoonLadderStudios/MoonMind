# Quickstart: Codex CLI OpenRouter Phase 1

1. Export `OPENROUTER_API_KEY` before starting the API service.
2. Start MoonMind normally.
3. Confirm the seeded provider profile `codex_openrouter_qwen36_plus` exists.
4. Submit a managed `codex_cli` run with `executionProfileRef = "codex_openrouter_qwen36_plus"`.
5. The launcher should generate `.moonmind/codex-home/config.toml`, set `CODEX_HOME`, inject `OPENROUTER_API_KEY`, and launch `codex exec` without a redundant default `-m`.
