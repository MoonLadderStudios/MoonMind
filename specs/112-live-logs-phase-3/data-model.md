# Data Model: Live Logs Phase 3

## Core Entities

### LogStreamEvent

Represents a single bounded event traversing the real-time publisher to a client.

- `sequence` (Integer): Monotonically increasing ID identifying the log payload inside this task run.
- `stream` (Enum: out, err, system): The source of the content (stdout, stderr, or MoonMind supervisor).
- `offset` (Integer): The cumulative byte or line offset marker mapping to the durable artifact position.
- `timestamp` (ISO8601 String): The exact moment the text was produced.
- `text` (String): The raw log data payload.

## Component Relationships

- **TaskRun** -> (1:1) **LogPublisher**: The active supervisor provisions a pub/sub mechanism representing the lifecycle of its processes.
- **LogPublisher** -> (1:N) **SSE Connections**: A publisher handles multiple active HTTP requests streaming out events.
