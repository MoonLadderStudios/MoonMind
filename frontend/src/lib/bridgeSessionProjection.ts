import { z } from 'zod';

export const BRIDGE_EVENT_PAGE_SCHEMA_VERSION = 'moonmind.bridge-session-events-page.v1' as const;
export const BRIDGE_RESOLUTION_SCHEMA_VERSION = 'moonmind.bridge-session-resolution.v1' as const;
export const BRIDGE_TERMINAL_SCHEMA_VERSION = 'moonmind.bridge-session-terminal.v1' as const;

export const BridgeEventSchema = z.object({
  id: z.string(),
  sequence: z.number().int().nonnegative(),
  timestamp: z.string(),
  stream: z.string(),
  text: z.string(),
  kind: z.string(),
  bridgeSessionId: z.string(),
}).passthrough();

export const BridgeTerminalEnvelopeSchema = z.object({
  schemaVersion: z.literal(BRIDGE_TERMINAL_SCHEMA_VERSION),
  status: z.string(),
  failureClass: z.string().nullable(),
  failureCode: z.string().nullable(),
  summary: z.string().nullable(),
  diagnosticsRef: z.string().nullable(),
  resourceRefs: z.record(z.string(), z.unknown()),
  cleanupState: z.string().nullable(),
  leaseReleaseState: z.string().nullable(),
  evidenceIncomplete: z.boolean(),
  explanation: z.string().nullable(),
});

export const BridgeEventPageSchema = z.object({
  schemaVersion: z.literal(BRIDGE_EVENT_PAGE_SCHEMA_VERSION),
  bridgeSessionId: z.string(),
  items: z.array(BridgeEventSchema),
  after: z.number().int().nonnegative(),
  nextCursor: z.string().nullable(),
  hasMore: z.boolean(),
  terminal: z.boolean(),
  latestSequence: z.number().int().nonnegative(),
  retentionGap: z.object({
    requestedAfter: z.number().int().nonnegative(),
    earliestAvailable: z.number().int().positive(),
    explanation: z.string(),
  }).nullable(),
  terminalEvidence: BridgeTerminalEnvelopeSchema.nullable(),
});

export const BridgeSessionResolutionSchema = z.object({
  schemaVersion: z.literal(BRIDGE_RESOLUTION_SCHEMA_VERSION),
  bridgeSessionId: z.string().min(1),
  workflowId: z.string(),
  runId: z.string().nullable(),
  stepExecutionId: z.string().nullable(),
  agentRunId: z.string(),
  status: z.string(),
  latestSequence: z.number().int().nonnegative(),
  providerProfileId: z.string().nullable(),
  hostId: z.string().nullable(),
  providerSessionId: z.string().nullable(),
  liveTailingAvailable: z.boolean(),
  terminalEvidenceAvailable: z.boolean(),
  resourceAvailability: z.record(z.string(), z.boolean()),
  compatibilityProfile: z.string(),
});

export function bridgeSessionRoute(
  apiBase: string,
  bridgeSessionId: string,
  suffix: 'events' | 'stream',
): string {
  const base = apiBase.endsWith('/') ? apiBase.slice(0, -1) : apiBase;
  return `${base}/omnigent/bridge-sessions/${encodeURIComponent(bridgeSessionId)}/${suffix}`;
}

export async function fetchBridgeEventPage(
  apiBase: string,
  bridgeSessionId: string,
  cursor?: string | null,
): Promise<z.infer<typeof BridgeEventPageSchema> | null> {
  const url = new URL(bridgeSessionRoute(apiBase, bridgeSessionId, 'events'), window.location.origin);
  if (cursor) url.searchParams.set('cursor', cursor);
  const response = await fetch(`${url.pathname}${url.search}`, { credentials: 'include' });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Bridge session events: ${response.status}`);
  return BridgeEventPageSchema.parse(await response.json());
}

export function bridgeEventStreamRoute(
  apiBase: string,
  bridgeSessionId: string,
  cursor?: string | null,
): string {
  const url = new URL(bridgeSessionRoute(apiBase, bridgeSessionId, 'stream'), window.location.origin);
  if (cursor) url.searchParams.set('cursor', cursor);
  return `${url.pathname}${url.search}`;
}
