# OAuth Terminal Architecture

MoonMind no longer uses external `tmate` sessions for managed-runtime OAuth
enrollment. The current first-party terminal contract is
[`OAuthTerminal.md`](./OAuthTerminal.md), which covers the session table, OAuth
session API, Mission Control terminal UI, PTY/WebSocket auth runner, and provider
registry for Codex CLI, Claude Code, and Gemini CLI.

This path is retained for roadmap and Jira references such as MM-738. New design
updates should be made in `OAuthTerminal.md`.
