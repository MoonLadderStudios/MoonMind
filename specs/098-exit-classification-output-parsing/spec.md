# Exit Classification & Output Parsing (Phase 4)

## Overview
Per-runtime correctness for exit codes and structured output parsing.

## Changes
- Created `RuntimeOutputParser` ABC with `PlainTextOutputParser` (default) and `NdjsonOutputParser` (Cursor)
- `CursorCliStrategy.classify_exit()` — NDJSON-aware with HTTP 429 rate-limit detection
- `CursorCliStrategy.create_output_parser()` — returns `NdjsonOutputParser`
- Default strategies (Gemini, Claude, Codex) use base `classify_exit()` and return `None` parser
- Added `output_parser.py` as shared module for all runtimes

## Tasks
- [x] Create `RuntimeOutputParser` protocol + `PlainTextOutputParser` + `NdjsonOutputParser`
- [x] Implement `CursorCliStrategy.classify_exit()` with rate-limit detection
- [x] Implement `CursorCliStrategy.create_output_parser()` returning NdjsonOutputParser
- [x] Add unit tests for all parsers and exit classification per runtime
- [x] Run full test suite
