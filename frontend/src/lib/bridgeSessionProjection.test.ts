import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  BridgeEventPageSchema,
  BridgeSessionResolutionSchema,
  bridgeEventStreamRoute,
  fetchBridgeEventPage,
} from './bridgeSessionProjection';

const event = {
  id: 'evt-1', sequence: 1, timestamp: '2026-07-16T00:00:00Z', stream: 'stdout',
  text: 'hello', kind: 'assistant_message', bridgeSessionId: 'brs-1',
};

const terminal = {
  schemaVersion: 'moonmind.bridge-session-terminal.v1', status: 'failed',
  failureClass: 'system_error', failureCode: 'host_failed', summary: 'failed early',
  diagnosticsRef: 'artifact://diag', resourceRefs: {}, cleanupState: null,
  leaseReleaseState: null, evidenceIncomplete: false, explanation: null,
};

const page = {
  schemaVersion: 'moonmind.bridge-session-events-page.v1', bridgeSessionId: 'brs-1',
  items: [event], after: 0, nextCursor: 'cursor-1', hasMore: false, terminal: true,
  latestSequence: 1, retentionGap: null, terminalEvidence: terminal,
};

describe('bridge session projection contract', () => {
  afterEach(() => vi.restoreAllMocks());

  it('validates page, stream, and terminal fallback fixtures', () => {
    expect(BridgeEventPageSchema.parse(page).terminalEvidence?.failureCode).toBe('host_failed');
    expect(bridgeEventStreamRoute('/api', 'brs-1', 'cursor-1')).toContain('cursor=cursor-1');
  });

  it('rejects unknown schema versions visibly', () => {
    expect(() => BridgeEventPageSchema.parse({ ...page, schemaVersion: 'future.v2' })).toThrow();
    expect(() => BridgeSessionResolutionSchema.parse({ schemaVersion: 'future.v2' })).toThrow();
  });

  it('returns null for an authorization-safe unknown response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 404 }));
    await expect(fetchBridgeEventPage('/api', 'unknown')).resolves.toBeNull();
  });

  it('validates fetched page fixtures', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(page));
    await expect(fetchBridgeEventPage('/api', 'brs-1')).resolves.toMatchObject({
      bridgeSessionId: 'brs-1', nextCursor: 'cursor-1',
    });
  });
});
