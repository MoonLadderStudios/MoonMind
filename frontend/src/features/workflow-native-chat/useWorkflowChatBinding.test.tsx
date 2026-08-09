import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MockInstance } from 'vitest';

import { cleanup, renderHook, waitFor } from '../../utils/test-utils';
import {
  ChatBindingRequestError,
  fetchWorkflowChatBinding,
  useWorkflowChatBinding,
} from './useWorkflowChatBinding';
import type { WorkflowChatBinding } from './chatBindingModel';

const AVAILABLE: WorkflowChatBinding = {
  chatBindingId: 'cb-1',
  workflowId: 'wf-1',
  runId: 'run-1',
  logicalStepId: 'step-a',
  chatUrl: '/omnigent-ui/workflow-chat/cb-1?embedded=1',
  apiBase: '/api/workflow-chat-bindings/cb-1/omnigent',
  state: 'available',
  readOnly: false,
  capabilities: { sendMessage: true },
};

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('fetchWorkflowChatBinding', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch');
    fetchSpy.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('bounds the request with an abort deadline and surfaces a retryable timeout error', async () => {
    vi.useFakeTimers();
    // The proxy never resolves; only the abort deadline ends the request.
    fetchSpy.mockImplementation(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'));
          });
        }),
    );

    const pending = fetchWorkflowChatBinding('/api', 'wf-1', 15000);
    const assertion = expect(pending).rejects.toMatchObject({
      status: 408,
      code: 'chat_binding_request_timeout',
    });
    await vi.advanceTimersByTimeAsync(15000);
    await assertion;
    await expect(pending).rejects.toBeInstanceOf(ChatBindingRequestError);
  });
});

describe('useWorkflowChatBinding polling posture', () => {
  let fetchSpy: MockInstance;

  function mockBinding(body: WorkflowChatBinding) {
    fetchSpy.mockImplementation(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        clone() {
          return { json: async () => body } as Response;
        },
        json: async () => body,
      } as unknown as Response),
    );
  }

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch');
    fetchSpy.mockClear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('keeps resolving the authoritative binding while the workflow is nonterminal', async () => {
    mockBinding(AVAILABLE);
    renderHook(
      () =>
        useWorkflowChatBinding({
          apiBase: '/api',
          workflowId: 'wf-1',
          enabled: true,
          workflowTerminal: false,
          pollIntervalMs: 50,
        }),
      { wrapper: wrapper() },
    );

    // Even though the binding already resolved to `available`, the resolver keeps
    // polling so a later Step's replacement session is discovered.
    await waitFor(() => expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(2), {
      timeout: 2000,
    });
  });

  it('stops resolving once the workflow is terminal', async () => {
    mockBinding({ ...AVAILABLE, state: 'ended', readOnly: true });
    renderHook(
      () =>
        useWorkflowChatBinding({
          apiBase: '/api',
          workflowId: 'wf-1',
          enabled: true,
          workflowTerminal: true,
          pollIntervalMs: 50,
        }),
      { wrapper: wrapper() },
    );

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    // A terminal workflow cannot spawn a new chat-capable session, so no further
    // polling occurs even after several would-be intervals elapse.
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
