import { describe, expect, it } from 'vitest';

import {
  deriveNativeChatStatus,
  fullPageChatHref,
  isMountableNativeChatStatus,
  nativeChatContextStatusLabel,
  nativeChatStateCopy,
  resolveSameOriginChatUrl,
  type WorkflowChatBinding,
} from './chatBindingModel';

const ORIGIN = 'https://moonmind.example';

function binding(overrides: Partial<WorkflowChatBinding>): WorkflowChatBinding {
  return {
    chatBindingId: 'cb-1',
    workflowId: 'wf-1',
    chatUrl: '/omnigent-ui/workflow-chat/cb-1?embedded=1',
    apiBase: '/api/workflow-chat-bindings/cb-1/omnigent',
    state: 'available',
    readOnly: false,
    capabilities: {},
    ...overrides,
  };
}

describe('deriveNativeChatStatus', () => {
  it('reports loading while the request is in flight with no data', () => {
    expect(
      deriveNativeChatStatus({ isLoading: true, binding: null, errorStatus: null }),
    ).toBe('loading');
  });

  it('maps writable and read-only available bindings', () => {
    expect(
      deriveNativeChatStatus({
        isLoading: false,
        binding: binding({ state: 'available', readOnly: false }),
        errorStatus: null,
      }),
    ).toBe('available');
    expect(
      deriveNativeChatStatus({
        isLoading: false,
        binding: binding({ state: 'available', readOnly: true }),
        errorStatus: null,
      }),
    ).toBe('readOnly');
  });

  it('maps starting and terminal states', () => {
    expect(
      deriveNativeChatStatus({
        isLoading: false,
        binding: binding({ state: 'starting', readOnly: true }),
        errorStatus: null,
      }),
    ).toBe('starting');
    expect(
      deriveNativeChatStatus({
        isLoading: false,
        binding: binding({ state: 'ended', readOnly: true }),
        errorStatus: null,
      }),
    ).toBe('terminal');
  });

  it('discriminates unavailable reasons', () => {
    expect(
      deriveNativeChatStatus({
        isLoading: false,
        binding: binding({ state: 'unavailable', unavailableReason: 'unsupported_runtime' }),
        errorStatus: null,
      }),
    ).toBe('unsupported');
    expect(
      deriveNativeChatStatus({
        isLoading: false,
        binding: binding({ state: 'unavailable', unavailableReason: 'no_session' }),
        errorStatus: null,
      }),
    ).toBe('missing');
    expect(
      deriveNativeChatStatus({
        isLoading: false,
        binding: binding({ state: 'unavailable', unavailableReason: 'session_cleaned_up' }),
        errorStatus: null,
      }),
    ).toBe('missing');
  });

  it('maps request errors to explicit statuses and fails closed on unknown states', () => {
    expect(
      deriveNativeChatStatus({ isLoading: false, binding: null, errorStatus: 401 }),
    ).toBe('unauthorized');
    expect(
      deriveNativeChatStatus({ isLoading: false, binding: null, errorStatus: 403 }),
    ).toBe('unauthorized');
    expect(
      deriveNativeChatStatus({ isLoading: false, binding: null, errorStatus: 404 }),
    ).toBe('notFound');
    expect(
      deriveNativeChatStatus({ isLoading: false, binding: null, errorStatus: 409 }),
    ).toBe('ambiguous');
    expect(
      deriveNativeChatStatus({ isLoading: false, binding: null, errorStatus: 503 }),
    ).toBe('unavailable');
    // Degraded provider input (unknown state) must not mount the native app.
    expect(
      deriveNativeChatStatus({
        isLoading: false,
        binding: binding({ state: 'weird' as WorkflowChatBinding['state'] }),
        errorStatus: null,
      }),
    ).toBe('unavailable');
  });
});

describe('isMountableNativeChatStatus', () => {
  it('mounts only available, read-only, and terminal', () => {
    expect(isMountableNativeChatStatus('available')).toBe(true);
    expect(isMountableNativeChatStatus('readOnly')).toBe(true);
    expect(isMountableNativeChatStatus('terminal')).toBe(true);
    for (const status of [
      'loading',
      'starting',
      'unsupported',
      'missing',
      'unauthorized',
      'notFound',
      'ambiguous',
      'incompatible',
      'disconnected',
      'unavailable',
    ] as const) {
      expect(isMountableNativeChatStatus(status)).toBe(false);
    }
  });
});

describe('resolveSameOriginChatUrl', () => {
  it('returns the same-origin relative path for a scoped url', () => {
    expect(
      resolveSameOriginChatUrl('/omnigent-ui/workflow-chat/cb-1?embedded=1', ORIGIN),
    ).toBe('/omnigent-ui/workflow-chat/cb-1?embedded=1');
  });

  it('rejects blank and cross-origin urls (never mounts upstream)', () => {
    expect(resolveSameOriginChatUrl('', ORIGIN)).toBeNull();
    expect(resolveSameOriginChatUrl(null, ORIGIN)).toBeNull();
    expect(
      resolveSameOriginChatUrl('https://upstream.omnigent.invalid/chat/xyz', ORIGIN),
    ).toBeNull();
  });
});

describe('fullPageChatHref', () => {
  it('drops the embedded flag but stays on the MoonMind origin', () => {
    expect(
      fullPageChatHref('/omnigent-ui/workflow-chat/cb-1?embedded=1', ORIGIN),
    ).toBe('/omnigent-ui/workflow-chat/cb-1');
  });

  it('refuses to construct an upstream url', () => {
    expect(
      fullPageChatHref('https://upstream.omnigent.invalid/chat/xyz?embedded=1', ORIGIN),
    ).toBeNull();
    expect(fullPageChatHref('', ORIGIN)).toBeNull();
  });
});

describe('nativeChatStateCopy', () => {
  it('announces transient states politely and failures assertively', () => {
    expect(nativeChatStateCopy('starting').role).toBe('status');
    expect(nativeChatStateCopy('loading').role).toBe('status');
    expect(nativeChatStateCopy('unauthorized').role).toBe('alert');
    expect(nativeChatStateCopy('incompatible').canRetry).toBe(true);
    expect(nativeChatStateCopy('unsupported').canRetry).toBe(false);
  });

  it('distinguishes cleaned-up terminal transcripts from missing sessions', () => {
    expect(nativeChatStateCopy('missing', 'session_cleaned_up').description).toMatch(
      /durable evidence/i,
    );
    expect(nativeChatStateCopy('missing', 'no_session').description).toMatch(
      /No native chat session/i,
    );
  });
});

describe('nativeChatContextStatusLabel', () => {
  it('labels read-only and terminal posture and hides for writable', () => {
    expect(nativeChatContextStatusLabel('available')).toBeNull();
    expect(nativeChatContextStatusLabel('readOnly')).toBe('Read-only');
    expect(nativeChatContextStatusLabel('terminal')).toMatch(/read-only/i);
    expect(nativeChatContextStatusLabel('disconnected')).toBe('Disconnected');
  });
});
