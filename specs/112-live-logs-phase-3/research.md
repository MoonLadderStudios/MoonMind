# Research: Phase 3 Streaming Transports

**Decision**: Implement SSE (`text/event-stream`)
**Rationale**: Native browser API `EventSource` supports SSE seamlessly. Easier to proxy through typical ingress configurations than WebSockets. MoonMind is a primarily HTTP-first API layer that doesn't need bi-directional log traffic (logging is inherently passive observation, as noted in Phase 5 docs). 
**Alternatives considered**: WebSockets, Long Polling. WebSockets add unnecessary complexity for unidirectional streaming.

**Decision**: In-memory Asyncio Queues / Redis PubSub for Fan-out
**Rationale**: A publisher object will maintain state in memory. If we scale out API workers later, Redis pubsub can share streams across instances. For Phase 3, we abstract the Publisher `subscribe` method so either backend works transparently.
**Alternatives considered**: ZeroMQ, Kafka (too heavy).
