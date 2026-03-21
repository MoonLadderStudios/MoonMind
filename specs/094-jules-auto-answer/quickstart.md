# Quickstart: Jules Question Auto-Answer

## What It Does

When Jules asks a question during workflow execution, MoonMind automatically generates an answer and sends it back — no human intervention needed.

## Configuration

Set these environment variables (all have sensible defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `JULES_AUTO_ANSWER_ENABLED` | `true` | Enable/disable auto-answering |
| `JULES_MAX_AUTO_ANSWERS` | `3` | Max questions per session before escalating |
| `JULES_AUTO_ANSWER_RUNTIME` | `llm` | Answer mode: `llm` (fast) or managed runtime ID |
| `JULES_AUTO_ANSWER_TIMEOUT_SECONDS` | `300` | Timeout per question-answer cycle |

## How It Works

1. Jules enters `AWAITING_USER_FEEDBACK` state
2. MoonMind detects the new `awaiting_feedback` normalized status
3. Fetches Jules's question via the Activities API
4. Sends question to LLM for a concise answer
5. Delivers the answer back to Jules via `sendMessage`
6. Jules resumes working

## Disabling

Set `JULES_AUTO_ANSWER_ENABLED=false` to disable. All Jules questions will require manual intervention via Mission Control.
