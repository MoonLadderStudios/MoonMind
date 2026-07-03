# Gemini Guidance

Use `AGENTS.md` as the primary MoonMind repository guidance file before making changes.

`AGENTS.md` now contains MoonMind principles, documentation precedence, testing discipline, Temporal troubleshooting, security guardrails, compatibility policy, and shared skill runtime rules.

MoonMind project principles are no longer kept in a standalone constitution file.

## Active Technologies
- TypeScript 5.9 with React 19.2 for dashboard UI; Python 3 FastAPI service for workflow console route handling. + React, Vite 8, TanStack Query, react-virtuoso, lucide-react, zod, FastAPI; planned optional leaf dependency `use-stick-to-bottom` if needed for chat autoscroll. No direct Omnigent React component, Tailwind 4, shadcn/Radix, Vercel AI SDK, or Omnigent event reducer dependency. (001-wire-chat-ui)
- N/A for new persisted data; chat state is derived from observability events, session snapshots, and transient optimistic client messages. Existing durable artifacts and observability history remain source evidence. (001-wire-chat-ui)

## Recent Changes
- 001-wire-chat-ui: Added TypeScript 5.9 with React 19.2 for dashboard UI; Python 3 FastAPI service for workflow console route handling. + React, Vite 8, TanStack Query, react-virtuoso, lucide-react, zod, FastAPI; planned optional leaf dependency `use-stick-to-bottom` if needed for chat autoscroll. No direct Omnigent React component, Tailwind 4, shadcn/Radix, Vercel AI SDK, or Omnigent event reducer dependency.
