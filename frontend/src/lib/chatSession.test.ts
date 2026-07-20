import { describe, expect, it } from 'vitest';
import {
  createChatSessionState,
  mapObservabilityEventToChatSessionEvent,
  projectChatSessionBlocks,
  reduceChatSessionEvents,
  seedOptimisticUserMessages,
  type RunObservabilityEventRow,
} from './chatSession';

const baseRow = {
  runId: 'agent-run-mm-1013',
  timestamp: '2026-06-28T00:00:00Z',
  stream: 'session',
  sessionId: 'sess-1',
  sessionEpoch: 3,
};

function row(
  sequence: number,
  kind: string,
  text: string,
  metadata: Record<string, unknown> = {},
): RunObservabilityEventRow {
  return {
    ...baseRow,
    sequence,
    kind,
    text,
    metadata,
  };
}

describe('chat session observability projection for MM-1013', () => {
  it('maps representative MoonMind observability rows into chat session event types', () => {
    const cases: Array<[RunObservabilityEventRow, string]> = [
      [row(1, 'user_message_submitted', 'hello'), 'user_message'],
      [row(2, 'turn_started', 'turn started'), 'response_started'],
      [row(3, 'assistant_message_delta', 'hel'), 'assistant_delta'],
      [row(4, 'assistant_message_completed', 'hello'), 'assistant_message'],
      [row(5, 'tool_call_started', 'pytest'), 'tool_call'],
      [row(6, 'tool_call_output', 'passed'), 'tool_result'],
      [row(7, 'approval_requested', 'approve?'), 'approval'],
      [row(8, 'session_cleared', 'cleared'), 'session_boundary'],
      [row(9, 'runtime_status', 'running'), 'runtime_status'],
      [row(10, 'turn_completed', 'done'), 'completion'],
      [row(11, 'turn_failed', 'failed'), 'failure'],
    ];

    expect(cases.map(([event]) => mapObservabilityEventToChatSessionEvent(event).type)).toEqual(
      cases.map(([, expected]) => expected),
    );
  });

  it('safely downgrades unknown or incomplete rows to system/status blocks', () => {
    const state = projectChatSessionBlocks([
      { runId: 'agent-run-mm-1013', sequence: 1, timestamp: 'bad', stream: 'session', text: 'mystery' },
    ]);

    expect(state.blocks).toMatchObject([
      {
        kind: 'system',
        text: 'mystery',
        agentRunId: 'agent-run-mm-1013',
        sequenceStart: 1,
        sequenceEnd: 1,
      },
    ]);
  });

  it('projects failed-launch lifecycle evidence into concise operator status blocks', () => {
    const state = projectChatSessionBlocks([
      row(1, 'lifecycle.profile_readiness', '', { status: 'ready' }),
      row(2, 'lifecycle.credential_preflight', '', {
        status: 'failed',
        code: 'oauth_generation_mismatch',
        summary: 'Credential generation did not match the mounted volume.',
        remediationAction: 'validate_codex_oauth',
        diagnosticsRef: 'artifact://launch/diagnostics',
        metadata: { providerProfileId: 'codex', hostLeaseRef: 'host-lease-1' },
      }),
      row(3, 'lifecycle.terminal', '', {
        status: 'failed',
        metadata: { cleanupCompleted: true, leaseReleased: true, workflowId: 'workflow-1' },
      }),
    ]);

    expect(state.blocks.map((block) => [block.kind, block.text])).toEqual([
      ['system', 'profile readiness: ready'],
      ['error', 'credential preflight: failed · Reason: oauth_generation_mismatch · Credential generation did not match the mounted volume. · Profile: codex · Host lease: host-lease-1 · Recommended action: validate codex oauth'],
      ['error', 'terminal: failed · Workflow: workflow-1 · Cleanup: completed · Profile lease: released'],
    ]);
    expect(state.blocks[1]?.metadata?.diagnosticsRef).toBe('artifact://launch/diagnostics');
  });

  it('maps runtime-neutral resource, diagnostics, control, and terminal vocabulary', () => {
    const cases: Array<[string, string]> = [
      ['resource_available', 'resource'],
      ['changed_file', 'resource'],
      ['diagnostics_available', 'diagnostic'],
      ['retention_gap', 'diagnostic'],
      ['elicitation_requested', 'control'],
      ['session_cancelled', 'failure'],
      ['session_timed_out', 'failure'],
      ['run_completed', 'completion'],
    ];

    expect(cases.map(([kind]) => mapObservabilityEventToChatSessionEvent(row(1, kind, kind)).type))
      .toEqual(cases.map(([, type]) => type));
  });

  it('renders execution-critical future schema events as incompatibilities', () => {
    const event = mapObservabilityEventToChatSessionEvent(
      row(1, 'future_required_event', 'Upgrade Workflow Chat to inspect this event.', {
        schemaVersion: 2,
        executionCritical: true,
      }),
    );
    const state = reduceChatSessionEvents(createChatSessionState(), [event]);

    expect(event.type).toBe('incompatible_schema');
    expect(state.blocks[0]).toMatchObject({
      kind: 'error',
      text: 'Upgrade Workflow Chat to inspect this event.',
    });
  });

  it('accumulates assistant deltas and dedupes a final assistant message', () => {
    const state = projectChatSessionBlocks([
      row(1, 'assistant_message_delta', 'hel', { responseId: 'resp-1', turnIndex: 0 }),
      row(2, 'assistant_message_delta', 'lo', { responseId: 'resp-1', turnIndex: 0 }),
      row(3, 'assistant_message_completed', 'hello', { responseId: 'resp-1', turnIndex: 0 }),
    ]);

    expect(state.blocks).toHaveLength(1);
    expect(state.blocks[0]).toMatchObject({
      kind: 'assistant',
      text: 'hello',
      status: 'completed',
      agentRunId: 'agent-run-mm-1013',
      sessionId: 'sess-1',
      sessionEpoch: 3,
      responseId: 'resp-1',
      turnIndex: 0,
      sequenceStart: 1,
      sequenceEnd: 3,
    });
  });

  it('projects direct-compat and Omnigent bridge journeys through the same chat path', () => {
    const journey = (source: 'codex_direct_compat' | 'omnigent') => [
      row(1, 'assistant_message_delta', 'working', { source, responseId: 'response-1' }),
      row(2, 'tool_call_started', 'Running tests', { source, callId: 'tool-1', toolName: 'pytest' }),
      row(3, 'tool_call_completed', 'passed', { source, callId: 'tool-1' }),
      row(4, 'approval_requested', 'Approval required.', { source, requestId: 'approval-1' }),
      row(5, 'resource_available', 'Summary published.', { source, artifactRef: 'artifact://summary/1' }),
      row(6, 'assistant_message_completed', 'working', { source, responseId: 'response-1' }),
      row(7, 'turn_completed', 'Complete.', { source, responseId: 'response-1' }),
    ];

    const direct = projectChatSessionBlocks(journey('codex_direct_compat'));
    const omnigent = projectChatSessionBlocks(journey('omnigent'));

    expect(direct.blocks.map(({ kind, text }) => ({ kind, text }))).toEqual(
      omnigent.blocks.map(({ kind, text }) => ({ kind, text })),
    );
    expect(direct.blocks.map((block) => block.kind)).toEqual([
      'assistant', 'tool', 'approval', 'system', 'status',
    ]);
  });

  it('pairs tool calls and results by callId and ignores duplicate call/result rows', () => {
    const state = projectChatSessionBlocks([
      row(1, 'tool_call_started', 'Running pytest', { responseId: 'resp-1', callId: 'call-1', toolName: 'pytest' }),
      row(1, 'tool_call_started', 'Running pytest', { responseId: 'resp-1', callId: 'call-1', toolName: 'pytest' }),
      row(2, 'tool_call_output', '2 passed', { responseId: 'resp-1', callId: 'call-1' }),
      row(2, 'tool_call_output', '2 passed', { responseId: 'resp-1', callId: 'call-1' }),
      row(3, 'tool_call_completed', 'pytest completed', { responseId: 'resp-1', callId: 'call-1' }),
    ]);

    expect(state.blocks).toHaveLength(1);
    expect(state.blocks[0]).toMatchObject({
      kind: 'tool',
      callId: 'call-1',
      toolName: 'pytest',
      text: 'Running pytest\n2 passed\npytest completed',
      sequenceStart: 1,
      sequenceEnd: 3,
    });
  });

  it('preserves split row identity so multiline chat rows are not deduped by sequence', () => {
    const state = projectChatSessionBlocks([
      { ...row(1, 'assistant_message_delta', 'first', { responseId: 'resp-1' }), id: '1-0-assistant_message_delta' },
      { ...row(1, 'assistant_message_delta', 'second', { responseId: 'resp-1' }), id: '1-1-assistant_message_delta' },
      { ...row(1, 'assistant_message_delta', 'third', { responseId: 'resp-1' }), id: '1-2-assistant_message_delta' },
    ]);

    expect(state.blocks).toHaveLength(1);
    const [block] = state.blocks;
    expect(block).toMatchObject({
      kind: 'assistant',
      text: 'firstsecondthird',
      sequenceStart: 1,
      sequenceEnd: 1,
    });
    expect(block?.sourceEventIds).toHaveLength(3);
  });

  it('keeps tool blocks turn-aware when response ids are not available', () => {
    const state = projectChatSessionBlocks([
      { ...row(1, 'tool_call_started', 'Running pytest', { callId: 'call-1', toolName: 'pytest' }), turnId: 'turn-1' },
      { ...row(2, 'tool_call_completed', 'pytest completed', { callId: 'call-1' }), turnId: 'turn-1' },
      { ...row(3, 'tool_call_started', 'Running pytest', { callId: 'call-1', toolName: 'pytest' }), turnId: 'turn-2' },
      { ...row(4, 'tool_call_completed', 'pytest completed', { callId: 'call-1' }), turnId: 'turn-2' },
    ]);

    expect(state.blocks).toHaveLength(2);
    expect(state.blocks.map((block) => block.sequenceStart)).toEqual([1, 3]);
    expect(state.blocks.map((block) => block.sequenceEnd)).toEqual([2, 4]);
  });

  it('projects approval events, boundaries, runtime completion, and failure blocks', () => {
    const state = projectChatSessionBlocks([
      row(1, 'approval_requested', 'Approval requested.', { responseId: 'resp-1' }),
      row(2, 'session_reset_boundary', 'Session reset.', { resetBoundaryRef: 'art-reset' }),
      row(3, 'runtime_status', 'Runtime healthy.'),
      row(4, 'turn_completed', 'Response complete.', { responseId: 'resp-2' }),
      row(5, 'turn_failed', 'Model stopped unexpectedly.', { responseId: 'resp-3' }),
    ]);

    expect(state.blocks.map((block) => block.kind)).toEqual([
      'approval',
      'boundary',
      'status',
      'status',
      'error',
    ]);
    expect(state.blocks[4]).toMatchObject({
      text: 'Model stopped unexpectedly.',
      agentRunId: 'agent-run-mm-1013',
    });
  });

  it('preserves ChatBlock context fields when available', () => {
    const state = projectChatSessionBlocks([
      row(7, 'user_message_submitted', 'Run the tests', {
        responseId: 'resp-ctx',
        itemId: 'item-ctx',
        turnIndex: 4,
      }),
    ]);

    expect(state.blocks[0]).toMatchObject({
      agentRunId: 'agent-run-mm-1013',
      sessionId: 'sess-1',
      sessionEpoch: 3,
      responseId: 'resp-ctx',
      itemId: 'item-ctx',
      turnIndex: 4,
      sequenceStart: 7,
      sequenceEnd: 7,
    });
  });

  it('converges when history is replayed before duplicate live SSE appends', () => {
    const history = [
      row(1, 'user_message_submitted', 'Start', { turnIndex: 0 }),
      row(2, 'assistant_message_delta', 'ok', { responseId: 'resp-1', turnIndex: 0 }),
    ];
    const liveDuplicate = row(2, 'assistant_message_delta', 'ok', { responseId: 'resp-1', turnIndex: 0 });
    const liveNext = row(3, 'assistant_message_completed', 'ok done', {
      responseId: 'resp-1',
      turnIndex: 0,
    });

    const state = reduceChatSessionEvents(
      reduceChatSessionEvents(
        createChatSessionState(),
        history.map((event) => mapObservabilityEventToChatSessionEvent(event)),
      ),
      [liveDuplicate, liveNext].map((event) => mapObservabilityEventToChatSessionEvent(event)),
    );

    expect(state.blocks.map((block) => [block.kind, block.text])).toEqual([
      ['user', 'Start'],
      ['assistant', 'ok done'],
    ]);
  });

  it('reconciles durable user message rows with optimistic user blocks', () => {
    const optimistic = seedOptimisticUserMessages(createChatSessionState(), [
      {
        key: 'client-msg-1',
        agentRunId: 'agent-run-mm-1013',
        sessionId: 'sess-1',
        sessionEpoch: 3,
        turnIndex: 1,
        text: 'Continue',
      },
    ]);

    const state = reduceChatSessionEvents(optimistic, [
      mapObservabilityEventToChatSessionEvent(
        row(10, 'user_message_submitted', 'Continue', {
          optimisticKey: 'client-msg-1',
          turnIndex: 1,
        }),
      ),
    ]);

    expect(state.blocks).toHaveLength(1);
    expect(state.blocks[0]).toMatchObject({
      kind: 'user',
      text: 'Continue',
      sequenceStart: 10,
      sequenceEnd: 10,
      metadata: expect.objectContaining({ reconciledFromOptimisticKey: 'client-msg-1' }),
    });
  });

  it('appends pending optimistic follow-ups after durable history', () => {
    const state = projectChatSessionBlocks(
      [
        row(1, 'user_message_submitted', 'Start', { turnIndex: 0 }),
        row(2, 'assistant_message_completed', 'Ready', { responseId: 'resp-1', turnIndex: 0 }),
      ],
      '',
      [
        {
          key: 'client-msg-2',
          agentRunId: 'agent-run-mm-1013',
          sessionId: 'sess-1',
          sessionEpoch: 3,
          turnIndex: 1,
          text: 'Continue',
        },
      ],
    );

    expect(state.blocks.map((block) => [block.kind, block.text])).toEqual([
      ['user', 'Start'],
      ['assistant', 'Ready'],
      ['user', 'Continue'],
    ]);
  });

  it('does not duplicate an optimistic message once durable history contains its client key', () => {
    const state = projectChatSessionBlocks(
      [
        row(10, 'user_message_submitted', 'Continue', {
          optimisticKey: 'client-msg-2',
          turnIndex: 1,
        }),
      ],
      '',
      [
        {
          key: 'client-msg-2',
          agentRunId: 'agent-run-mm-1013',
          sessionId: 'sess-1',
          sessionEpoch: 3,
          turnIndex: 1,
          text: 'Continue',
        },
      ],
    );

    expect(state.blocks).toHaveLength(1);
    expect(state.blocks[0]).toMatchObject({
      kind: 'user',
      text: 'Continue',
      sequenceStart: 10,
    });
  });
});
