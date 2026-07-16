// MM-1013 / source MM-977: MoonMind managed-session observability to chat projection.
export type RunObservabilityEventRow = {
  id?: string | null;
  runId?: string | null;
  agentRunId?: string | null;
  sequence?: number | null;
  timestamp?: string | null;
  stream?: string | null;
  text?: string | null;
  kind?: string | null;
  sessionId?: string | null;
  session_id?: string | null;
  sessionEpoch?: number | null;
  session_epoch?: number | null;
  turnId?: string | null;
  turn_id?: string | null;
  activeTurnId?: string | null;
  active_turn_id?: string | null;
  metadata?: Record<string, unknown> | null;
};

export type ChatSessionEventType =
  | 'user_message'
  | 'response_started'
  | 'assistant_delta'
  | 'assistant_message'
  | 'tool_call'
  | 'tool_result'
  | 'approval'
  | 'session_boundary'
  | 'runtime_status'
  | 'completion'
  | 'failure'
  | 'resource'
  | 'diagnostic'
  | 'control'
  | 'incompatible_schema'
  | 'system_status';

export type ChatBlockKind =
  | 'user'
  | 'assistant'
  | 'tool'
  | 'approval'
  | 'system'
  | 'status'
  | 'error'
  | 'boundary';

export type ChatBlockContext = {
  agentRunId: string;
  sessionId?: string | undefined;
  sessionEpoch?: number | undefined;
  responseId?: string | undefined;
  itemId?: string | undefined;
  turnIndex?: number | undefined;
  sequenceStart?: number | undefined;
  sequenceEnd?: number | undefined;
};

export type ChatSessionEvent = ChatBlockContext & {
  id: string;
  type: ChatSessionEventType;
  text: string;
  timestamp?: string | undefined;
  sourceKind?: string | undefined;
  turnId?: string | undefined;
  activeTurnId?: string | undefined;
  callId?: string | undefined;
  toolName?: string | undefined;
  status?: string | undefined;
  metadata: Record<string, unknown>;
  optimisticKey?: string | undefined;
};

export type ChatBlock = ChatBlockContext & {
  id: string;
  kind: ChatBlockKind;
  text: string;
  status?: string | undefined;
  timestamp?: string | undefined;
  sourceEventIds: string[];
  metadata?: Record<string, unknown> | undefined;
  callId?: string | undefined;
  toolName?: string | undefined;
};

export type ChatSessionState = {
  blocks: ChatBlock[];
  seenEventIds: Set<string>;
  blockIdByOptimisticKey: Map<string, string>;
  openAssistantBlockIdByScope: Map<string, string>;
  toolBlockIdByScope: Map<string, string>;
};

export type OptimisticUserMessage = {
  key: string;
  agentRunId: string;
  text: string;
  sessionId?: string | undefined;
  sessionEpoch?: number | undefined;
  turnIndex?: number | undefined;
  timestamp?: string | undefined;
};

const USER_MESSAGE_KINDS = new Set(['user_message_submitted']);
const RESPONSE_STARTED_KINDS = new Set(['turn_started', 'response_started']);
const ASSISTANT_DELTA_KINDS = new Set(['assistant_message_delta']);
const ASSISTANT_FINAL_KINDS = new Set(['assistant_message_completed', 'assistant_message']);
const TOOL_CALL_KINDS = new Set(['tool_call_started']);
const TOOL_RESULT_KINDS = new Set([
  'tool_call_progress', 'tool_call_output', 'tool_call_completed', 'tool_call_failed',
  'session_item_started', 'session_item_progress', 'session_item_output', 'session_item_completed',
  'session_item_failed',
]);
const APPROVAL_KINDS = new Set([
  'approval_requested',
  'approval_granted',
  'approval_denied',
  'approval_resolved',
  'intervention_requested',
  'intervention_resolved',
]);
const BOUNDARY_KINDS = new Set([
  'session_started',
  'session_resumed',
  'session_cleared',
  'session_terminated',
  'session_reset_boundary',
]);
const STATUS_KINDS = new Set([
  'runtime_status', 'model_status', 'system_annotation', 'host_ready', 'session_ready',
  'readiness_changed',
]);
const COMPLETION_KINDS = new Set([
  'turn_completed', 'response_completed', 'session_completed', 'run_completed', 'completion',
]);
const FAILURE_KINDS = new Set([
  'turn_failed',
  'turn_interrupted',
  'response_failed',
  'tool_call_failed',
  'empty_assistant_turn_detected',
  'session_failed', 'run_failed', 'cancelled', 'canceled', 'session_cancelled',
  'session_canceled', 'timeout', 'timed_out', 'session_timed_out', 'interrupted',
  'stop_requested', 'stopped',
]);
const RESOURCE_KINDS = new Set([
  'resource_available', 'resource_published', 'changed_file', 'changed_files',
  'snapshot_available', 'manifest_available', 'artifact_available',
]);
const DIAGNOSTIC_KINDS = new Set([
  'diagnostic', 'diagnostics_available', 'evidence_degraded', 'retention_gap',
  'contract_drift', 'cleanup_completed', 'cleanup_failed',
]);
const CONTROL_KINDS = new Set([
  'elicitation_requested', 'elicitation_resolved', 'interrupt_requested', 'interrupt_resolved',
  'stop_requested', 'cancel_requested',
]);
const SUPPORTED_SCHEMA_VERSION = 1;

export function createChatSessionState(): ChatSessionState {
  return {
    blocks: [],
    seenEventIds: new Set(),
    blockIdByOptimisticKey: new Map(),
    openAssistantBlockIdByScope: new Map(),
    toolBlockIdByScope: new Map(),
  };
}

export function seedOptimisticUserMessages(
  state: ChatSessionState,
  messages: OptimisticUserMessage[],
): ChatSessionState {
  return messages.reduce((next, message) => {
    if (next.blockIdByOptimisticKey.has(message.key)) return next;
    const block: ChatBlock = {
      id: `optimistic:user:${message.key}`,
      kind: 'user',
      text: message.text,
      timestamp: message.timestamp,
      sourceEventIds: [],
      agentRunId: message.agentRunId,
      sessionId: message.sessionId,
      sessionEpoch: message.sessionEpoch,
      turnIndex: message.turnIndex,
      metadata: { optimisticKey: message.key },
    };
    const updated = cloneState(next);
    updated.blocks = [...updated.blocks, block];
    updated.blockIdByOptimisticKey.set(message.key, block.id);
    return updated;
  }, state);
}

export function mapObservabilityEventToChatSessionEvent(
  row: RunObservabilityEventRow,
  fallbackAgentRunId = '',
): ChatSessionEvent {
  const metadata = asRecord(row.metadata);
  const kind = stringOrUndefined(row.kind);
  const agentRunId = firstString(row.agentRunId, row.runId, fallbackAgentRunId) || 'unknown-agent-run';
  const sessionId = firstString(row.sessionId, row.session_id);
  const sessionEpoch = firstNumber(row.sessionEpoch, row.session_epoch);
  const turnIndex = firstNumber(metadata.turnIndex, metadata.turn_index);
  const turnId = firstString(row.turnId, row.turn_id, metadata.turnId, metadata.turn_id);
  const activeTurnId = firstString(
    row.activeTurnId,
    row.active_turn_id,
    metadata.activeTurnId,
    metadata.active_turn_id,
  );
  const sequence = firstNumber(row.sequence);
  const responseId = firstString(metadata.responseId, metadata.response_id, metadata.responseID);
  const itemId = firstString(metadata.itemId, metadata.item_id, metadata.itemID);
  const callId = firstString(metadata.callId, metadata.call_id, metadata.callID, itemId);
  const sourceKind = kind || 'unknown';
  const text = stringOrUndefined(row.text) || firstString(metadata.text, metadata.message, metadata.summary) || '';
  const event: ChatSessionEvent = {
    id: eventId({
      row,
      metadata,
      sourceKind,
      agentRunId,
      responseId,
      itemId,
      callId,
      turnId,
      activeTurnId,
    }),
    type: eventTypeForKind(sourceKind, row.stream, metadata),
    text,
    timestamp: stringOrUndefined(row.timestamp),
    sourceKind,
    turnId,
    activeTurnId,
    callId,
    toolName: firstString(metadata.toolName, metadata.tool_name, metadata.name),
    status: firstString(metadata.status, metadata.state, sourceKind),
    metadata,
    agentRunId,
    sessionId,
    sessionEpoch,
    responseId,
    itemId,
    turnIndex,
    sequenceStart: sequence,
    sequenceEnd: sequence,
    optimisticKey: firstString(metadata.optimisticKey, metadata.clientMessageId, metadata.client_message_id),
  };
  return stripUndefined(event);
}

export function reduceChatSessionEvents(
  state: ChatSessionState,
  events: ChatSessionEvent[],
): ChatSessionState {
  return events.reduce(reduceChatSessionEvent, state);
}

export function projectChatSessionBlocks(
  rows: RunObservabilityEventRow[],
  fallbackAgentRunId = '',
  optimisticMessages: OptimisticUserMessage[] = [],
): ChatSessionState {
  const projected = reduceChatSessionEvents(
    createChatSessionState(),
    rows.map((row) => mapObservabilityEventToChatSessionEvent(row, fallbackAgentRunId)),
  );
  const pendingOptimisticMessages = optimisticMessages.filter((message) => (
    !projected.blocks.some((block) => (
      block.kind === 'user' &&
      (
        block.metadata?.optimisticKey === message.key ||
        block.metadata?.clientMessageId === message.key ||
        block.metadata?.client_message_id === message.key
      )
    ))
  ));
  return seedOptimisticUserMessages(projected, pendingOptimisticMessages);
}

export function reduceChatSessionEvent(
  state: ChatSessionState,
  event: ChatSessionEvent,
): ChatSessionState {
  if (state.seenEventIds.has(event.id)) return state;
  const next = cloneState(state);
  next.seenEventIds.add(event.id);

  switch (event.type) {
    case 'user_message':
      upsertUserBlock(next, event);
      break;
    case 'response_started':
      closeAssistant(next, event);
      appendBlock(next, event, 'status', event.text || 'Response started.');
      break;
    case 'assistant_delta':
      upsertAssistantBlock(next, event, { append: true });
      break;
    case 'assistant_message':
      upsertAssistantBlock(next, event, { append: false });
      closeAssistant(next, event);
      break;
    case 'tool_call':
      upsertToolBlock(next, event, event.text || event.toolName || 'Tool call started.');
      break;
    case 'tool_result':
      upsertToolBlock(next, event, event.text || event.status || 'Tool result received.');
      if (event.sourceKind !== 'tool_call_output') {
        closeTool(next, event);
      }
      break;
    case 'approval':
      appendBlock(next, event, 'approval', event.text || event.status || 'Approval event.');
      break;
    case 'session_boundary':
      closeAssistant(next, event);
      next.toolBlockIdByScope.clear();
      appendBlock(next, event, 'boundary', event.text || event.status || 'Session boundary.');
      break;
    case 'runtime_status':
    case 'completion':
      closeAssistant(next, event);
      next.toolBlockIdByScope.clear();
      appendBlock(next, event, 'status', event.text || event.status || 'Runtime status.');
      break;
    case 'failure':
      if (event.sourceKind === 'tool_call_failed') {
        upsertToolBlock(next, event, event.text || event.status || 'Tool failed.');
        closeTool(next, event);
      }
      closeAssistant(next, event);
      next.toolBlockIdByScope.clear();
      appendBlock(next, event, 'error', event.text || event.status || 'Run failed.');
      break;
    case 'incompatible_schema':
      appendBlock(next, event, 'error', event.text || 'This event requires a newer Workflow Chat schema.');
      break;
    case 'resource':
    case 'diagnostic':
    case 'control':
      appendBlock(next, event, 'system', event.text || event.status || 'Session evidence updated.');
      break;
    case 'system_status':
      appendBlock(next, event, 'system', event.text || event.status || 'System event.');
      break;
  }

  return next;
}

function upsertUserBlock(state: ChatSessionState, event: ChatSessionEvent): void {
  const optimisticKey = event.optimisticKey || optimisticKeyForText(event);
  const optimisticBlockId = optimisticKey ? state.blockIdByOptimisticKey.get(optimisticKey) : undefined;
  if (optimisticKey && optimisticBlockId) {
    updateBlock(state, optimisticBlockId, (block) => ({
      ...mergeContext(block, event),
      id: durableBlockId('user', event),
      text: event.text || block.text,
      sourceEventIds: [...block.sourceEventIds, event.id],
      metadata: { ...block.metadata, ...event.metadata, reconciledFromOptimisticKey: optimisticKey },
    }));
    state.blockIdByOptimisticKey.set(optimisticKey, durableBlockId('user', event));
    return;
  }
  appendBlock(state, event, 'user', event.text);
}

function upsertAssistantBlock(
  state: ChatSessionState,
  event: ChatSessionEvent,
  options: { append: boolean },
): void {
  const scope = responseScope(event);
  const existingId = state.openAssistantBlockIdByScope.get(scope);
  if (existingId) {
    updateBlock(state, existingId, (block) => {
      const text = options.append ? block.text + event.text : dedupFinalAssistantText(block.text, event.text);
      return {
        ...mergeContext(block, event),
        text,
        status: event.type === 'assistant_message' ? 'completed' : 'streaming',
        sourceEventIds: [...block.sourceEventIds, event.id],
        metadata: { ...block.metadata, ...event.metadata },
      };
    });
    return;
  }
  const block = blockFromEvent(event, 'assistant', event.text);
  block.status = event.type === 'assistant_message' ? 'completed' : 'streaming';
  state.blocks = [...state.blocks, block];
  if (event.type === 'assistant_delta') {
    state.openAssistantBlockIdByScope.set(scope, block.id);
  }
}

function upsertToolBlock(state: ChatSessionState, event: ChatSessionEvent, text: string): void {
  const scope = toolScope(event);
  const existingId = state.toolBlockIdByScope.get(scope);
  if (existingId) {
    updateBlock(state, existingId, (block) => ({
      ...mergeContext(block, event),
      text: appendLine(block.text, text),
      status: event.status,
      sourceEventIds: [...block.sourceEventIds, event.id],
      metadata: { ...block.metadata, ...event.metadata },
      callId: event.callId || block.callId,
      toolName: event.toolName || block.toolName,
    }));
    return;
  }
  const block = blockFromEvent(event, 'tool', text);
  block.callId = event.callId;
  block.toolName = event.toolName;
  state.blocks = [...state.blocks, block];
  state.toolBlockIdByScope.set(scope, block.id);
}

function appendBlock(
  state: ChatSessionState,
  event: ChatSessionEvent,
  kind: ChatBlockKind,
  text: string,
): void {
  state.blocks = [...state.blocks, blockFromEvent(event, kind, text)];
}

function updateBlock(
  state: ChatSessionState,
  blockId: string,
  update: (block: ChatBlock) => ChatBlock,
): void {
  state.blocks = state.blocks.map((block) => (block.id === blockId ? stripUndefined(update(block)) : block));
}

function blockFromEvent(event: ChatSessionEvent, kind: ChatBlockKind, text: string): ChatBlock {
  return stripUndefined({
    id: durableBlockId(kind, event),
    kind,
    text,
    status: event.status,
    timestamp: event.timestamp,
    sourceEventIds: [event.id],
    metadata: event.metadata,
    callId: event.callId,
    toolName: event.toolName,
    agentRunId: event.agentRunId,
    sessionId: event.sessionId,
    sessionEpoch: event.sessionEpoch,
    responseId: event.responseId,
    itemId: event.itemId,
    turnIndex: event.turnIndex,
    sequenceStart: event.sequenceStart,
    sequenceEnd: event.sequenceEnd,
  });
}

function mergeContext(block: ChatBlock, event: ChatSessionEvent): ChatBlock {
  return stripUndefined({
    ...block,
    agentRunId: block.agentRunId || event.agentRunId,
    sessionId: block.sessionId || event.sessionId,
    sessionEpoch: block.sessionEpoch ?? event.sessionEpoch,
    responseId: block.responseId || event.responseId,
    itemId: block.itemId || event.itemId,
    turnIndex: block.turnIndex ?? event.turnIndex,
    sequenceStart: minDefined(block.sequenceStart, event.sequenceStart),
    sequenceEnd: maxDefined(block.sequenceEnd, event.sequenceEnd),
    timestamp: block.timestamp || event.timestamp,
  });
}

function closeAssistant(state: ChatSessionState, event: ChatSessionEvent): void {
  state.openAssistantBlockIdByScope.delete(responseScope(event));
}

function closeTool(state: ChatSessionState, event: ChatSessionEvent): void {
  state.toolBlockIdByScope.delete(toolScope(event));
}

function eventTypeForKind(
  kind: string,
  stream: string | null | undefined,
  metadata: Record<string, unknown>,
): ChatSessionEventType {
  if (isCriticalSchemaIncompatible(kind, metadata)) return 'incompatible_schema';
  if (USER_MESSAGE_KINDS.has(kind)) return 'user_message';
  if (RESPONSE_STARTED_KINDS.has(kind)) return 'response_started';
  if (ASSISTANT_DELTA_KINDS.has(kind)) return 'assistant_delta';
  if (ASSISTANT_FINAL_KINDS.has(kind)) return 'assistant_message';
  if (TOOL_CALL_KINDS.has(kind)) return 'tool_call';
  if (TOOL_RESULT_KINDS.has(kind)) return FAILURE_KINDS.has(kind) ? 'failure' : 'tool_result';
  if (APPROVAL_KINDS.has(kind)) return 'approval';
  if (BOUNDARY_KINDS.has(kind)) return 'session_boundary';
  if (STATUS_KINDS.has(kind)) return 'runtime_status';
  if (COMPLETION_KINDS.has(kind)) return 'completion';
  if (FAILURE_KINDS.has(kind)) return 'failure';
  if (RESOURCE_KINDS.has(kind)) return 'resource';
  if (DIAGNOSTIC_KINDS.has(kind)) return 'diagnostic';
  if (CONTROL_KINDS.has(kind)) return 'control';
  return stream === 'stderr' ? 'failure' : 'system_status';
}

function isCriticalSchemaIncompatible(kind: string, metadata: Record<string, unknown>): boolean {
  if (kind === 'incompatible_schema' || kind === 'schema_incompatible') return true;
  const version = firstNumber(metadata.schemaVersion, metadata.schema_version);
  const critical = metadata.executionCritical === true || metadata.execution_critical === true;
  return critical && version !== undefined && version > SUPPORTED_SCHEMA_VERSION;
}

function eventId({
  row,
  metadata,
  sourceKind,
  agentRunId,
  responseId,
  itemId,
  callId,
  turnId,
  activeTurnId,
}: {
  row: RunObservabilityEventRow;
  metadata: Record<string, unknown>;
  sourceKind: string;
  agentRunId: string;
  responseId?: string | undefined;
  itemId?: string | undefined;
  callId?: string | undefined;
  turnId?: string | undefined;
  activeTurnId?: string | undefined;
}): string {
  const explicit = firstString(metadata.eventId, metadata.event_id, metadata.id);
  if (explicit) return `${agentRunId}:${sourceKind}:${explicit}`;
  const rowId = firstString(row.id);
  if (rowId) return `${agentRunId}:${sourceKind}:${rowId}`;
  const sequence = firstNumber(row.sequence);
  if (sequence !== undefined) return `${agentRunId}:seq:${sequence}:${sourceKind}`;
  return [
    agentRunId,
    sourceKind,
    turnId || activeTurnId || '-',
    responseId || '-',
    itemId || '-',
    callId || '-',
    firstString(row.text) || '',
  ].join(':');
}

function durableBlockId(kind: ChatBlockKind, event: ChatSessionEvent): string {
  if (kind === 'assistant') return `assistant:${responseScope(event)}`;
  if (kind === 'tool') return `tool:${toolScope(event)}`;
  return [
    kind,
    event.agentRunId,
    event.sessionId || '-',
    event.sessionEpoch ?? '-',
    event.turnIndex ?? '-',
    event.responseId || '-',
    event.itemId || '-',
    event.sequenceStart ?? event.id,
  ].join(':');
}

function responseScope(event: ChatSessionEvent): string {
  return [
    event.agentRunId,
    event.sessionId || '-',
    event.sessionEpoch ?? '-',
    event.turnIndex ?? '-',
    event.responseId ||
      event.itemId ||
      event.activeTurnId ||
      event.turnId ||
      firstString(event.metadata.activeTurnId, event.metadata.turnId) ||
      '-',
  ].join(':');
}

function toolScope(event: ChatSessionEvent): string {
  return [
    responseScope(event),
    event.callId || event.itemId || event.toolName || event.id,
  ].join(':');
}

function dedupFinalAssistantText(existing: string, finalText: string): string {
  if (!finalText) return existing;
  if (!existing) return finalText;
  if (finalText === existing || finalText.startsWith(existing)) return finalText;
  if (existing.endsWith(finalText)) return existing;
  return appendLine(existing, finalText);
}

function appendLine(existing: string, next: string): string {
  if (!existing) return next;
  if (!next || existing.includes(next)) return existing;
  return `${existing}\n${next}`;
}

function optimisticKeyForText(event: ChatSessionEvent): string | undefined {
  const normalizedText = event.text.trim();
  if (!normalizedText) return undefined;
  return [
    event.agentRunId,
    event.sessionId || '-',
    event.sessionEpoch ?? '-',
    event.turnIndex ?? '-',
    normalizedText,
  ].join(':');
}

function cloneState(state: ChatSessionState): ChatSessionState {
  return {
    blocks: state.blocks,
    seenEventIds: new Set(state.seenEventIds),
    blockIdByOptimisticKey: new Map(state.blockIdByOptimisticKey),
    openAssistantBlockIdByScope: new Map(state.openAssistantBlockIdByScope),
    toolBlockIdByScope: new Map(state.toolBlockIdByScope),
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? { ...(value as Record<string, unknown>) }
    : {};
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    const normalized = stringOrUndefined(value);
    if (normalized) return normalized;
  }
  return undefined;
}

function stringOrUndefined(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

function firstNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return undefined;
}

function minDefined(left: number | undefined, right: number | undefined): number | undefined {
  if (left === undefined) return right;
  if (right === undefined) return left;
  return Math.min(left, right);
}

function maxDefined(left: number | undefined, right: number | undefined): number | undefined {
  if (left === undefined) return right;
  if (right === undefined) return left;
  return Math.max(left, right);
}

function stripUndefined<T extends object>(value: T): T {
  return Object.fromEntries(
    Object.entries(value).filter(([, entryValue]) => entryValue !== undefined),
  ) as T;
}
