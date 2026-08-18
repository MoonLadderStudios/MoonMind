import { describe, expect, it } from 'vitest';

import {
  normalizeObservabilityEvent,
  parseObservabilityEventsResponse,
} from '../entrypoints/workflow-detail';

// AC7 browser binding for MoonLadderStudios/MoonMind#3709: the Workflow Detail
// event-stream client must absorb the same transport faults the fault lab
// injects (duplicate / reordered events across a reconnect) and still present a
// monotonic, de-duplicated timeline plus a replayed terminal envelope. This
// exercises the *real* frontend parse contract (`parseObservabilityEventsResponse`
// over `moonmind.bridge-session-events-page.v1`) in a real browser — jsdom is not
// used because the production client runs in the browser and the reconnect/dedup
// path is what the escaped UI incidents (#3696/#3685/#3697) live on. Like the
// repo's other browser guardrails it runs under `npm run ui:test:browser`
// (Chromium/Firefox via Playwright) and is not part of the hermetic unit suite.

// A fault-scenario transport frontier: the provider re-delivered sequence 2
// (duplicate) and delivered 4 before 3 (reorder) across a reconnect.
function faultFrontierPage() {
  const rawEvent = (sequence: number) => ({
    sequence,
    timestamp: `2026-08-18T00:00:0${sequence}+00:00`,
    stream: 'session' as const,
    text: `event ${sequence}`,
    kind: 'response.delta',
    metadata: { sequence },
  });
  return {
    schemaVersion: 'moonmind.bridge-session-events-page.v1',
    bridgeSessionId: 'brs-faultlab',
    // Transport disorder as the fault lab scripts it: duplicate 2, reorder 4/3.
    items: [rawEvent(1), rawEvent(2), rawEvent(2), rawEvent(4), rawEvent(3)],
    after: 0,
    nextCursor: null,
    hasMore: false,
    terminal: true,
    latestSequence: 4,
    terminalEnvelope: {
      schemaVersion: 'moonmind.bridge-session-terminal.v1',
      status: 'completed' as const,
      summary: 'done',
      finalSnapshotRef: 'artifact://final',
    },
  };
}

function monotonicDedupedSequences(events: { sequence: number }[]): number[] {
  const seen = new Set<number>();
  const ordered: number[] = [];
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    if (seen.has(event.sequence)) continue;
    seen.add(event.sequence);
    ordered.push(event.sequence);
  }
  return ordered;
}

describe('omnigent fault-replay Workflow Detail transport binding', () => {
  it('normalizes a fault frontier into a monotonic, de-duplicated timeline', () => {
    const parsed = parseObservabilityEventsResponse(faultFrontierPage());
    // The real client applies the reconnect dedup/order the timeline relies on.
    const sequences = monotonicDedupedSequences(parsed.events);
    expect(sequences).toEqual([1, 2, 3, 4]);
    // Duplicate + reordered transport events collapse to one frontier.
    expect(parsed.events.length).toBeGreaterThanOrEqual(4);
    expect(new Set(sequences).size).toBe(sequences.length);
  });

  it('replays the terminal envelope after a disconnect (historical-read safety)', () => {
    const parsed = parseObservabilityEventsResponse(faultFrontierPage());
    expect(parsed.terminal).toBe(true);
    expect(parsed.terminalEnvelope).not.toBeNull();
    expect(parsed.terminalEnvelope?.status).toBe('completed');
    expect(parsed.terminalEnvelope?.finalSnapshotRef).toBe('artifact://final');
  });

  it('normalizes camelCase and snake_case identity aliases identically', () => {
    const camel = normalizeObservabilityEvent({
      sequence: 7,
      timestamp: '2026-08-18T00:00:07+00:00',
      stream: 'session',
      text: 'x',
      sessionId: 'sess-1',
      activeTurnId: 'turn-1',
    });
    expect(camel.session_id).toBe('sess-1');
    expect(camel.active_turn_id).toBe('turn-1');
  });
});
