/**
 * Tests for the native Omnigent Workflow Chat surface
 * (MoonLadderStudios/MoonMind#3638).
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, waitFor } from '@testing-library/react';

import { renderWithClient } from '../utils/test-utils';
import {
  WorkflowChatNative,
  fullPageChatUrl,
  type WorkflowChatBinding,
} from './WorkflowChatNative';

const API_BASE = '/api';
const WORKFLOW_ID = 'mm:w1';
const CHAT_URL = '/omnigent-ui/workflow-chat/chatb_opaque123?embedded=1';

function bindingResponse(overrides: Partial<WorkflowChatBinding> = {}): WorkflowChatBinding {
  return {
    chatBindingId: 'chatb_opaque123',
    workflowId: WORKFLOW_ID,
    chatUrl: CHAT_URL,
    apiBase: '/api/workflow-chat-bindings/chatb_opaque123/omnigent',
    state: 'available',
    readOnly: false,
    capabilities: {},
    ...overrides,
  } as WorkflowChatBinding;
}

function mockFetch(body: WorkflowChatBinding | null, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      ({
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      }) as unknown as Response,
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('fullPageChatUrl', () => {
  it('drops only the embedded flag from the scoped chat url', () => {
    expect(fullPageChatUrl(CHAT_URL)).toBe(
      '/omnigent-ui/workflow-chat/chatb_opaque123',
    );
  });

  it('keeps other query parameters and never points at the upstream server', () => {
    expect(
      fullPageChatUrl('/omnigent-ui/workflow-chat/x?embedded=1&foo=bar'),
    ).toBe('/omnigent-ui/workflow-chat/x?foo=bar');
  });
});

describe('WorkflowChatNative', () => {
  it('embeds the native app in an iframe using the scoped chatUrl', async () => {
    mockFetch(bindingResponse());
    const { getByTestId, queryByText } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    const frame = await waitFor(() => getByTestId('workflow-native-chat-frame'));
    expect(frame.getAttribute('src')).toBe(CHAT_URL);
    // The legacy projection is not rendered once native chat is available (no
    // second composer competing with the native UI).
    expect(queryByText('legacy projection')).toBeNull();
  });

  it('offers an Open in Omnigent full-page link on the same scoped binding', async () => {
    mockFetch(bindingResponse());
    const { getByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active />,
    );

    const link = await waitFor(() => getByTestId('workflow-native-chat-open'));
    expect(link.getAttribute('href')).toBe(
      '/omnigent-ui/workflow-chat/chatb_opaque123',
    );
    expect(link.getAttribute('href')).not.toContain('embedded');
  });

  it('falls back to the legacy projection when no native binding exists', async () => {
    mockFetch(null, 404);
    const { findByText, queryByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    expect(await findByText('legacy projection')).toBeTruthy();
    expect(queryByTestId('workflow-native-chat-frame')).toBeNull();
  });

  it('shows the unavailable reason, a Retry, and still renders the fallback', async () => {
    mockFetch(
      bindingResponse({ state: 'unavailable', unavailableReason: 'native_ui_upstream_unavailable', chatUrl: '' }),
    );
    const { findByTestId, findByText } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    expect(await findByTestId('workflow-native-chat-unavailable')).toBeTruthy();
    // The unavailable state is retryable, so a stable Retry action is offered.
    expect(await findByTestId('workflow-native-chat-retry')).toBeTruthy();
    expect(await findByText('legacy projection')).toBeTruthy();
  });

  it('surfaces a stable reason and Retry when the binding request is unreachable', async () => {
    // A non-404 error makes fetchWorkflowChatBinding throw, so the query errors.
    mockFetch(null, 500);
    const { findByTestId, findByText } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    const unavailable = await findByTestId('workflow-native-chat-unavailable');
    expect(unavailable.textContent).toContain('native_chat_binding_unreachable');
    expect(await findByTestId('workflow-native-chat-retry')).toBeTruthy();
    // Native failure still shows the read-only fallback rather than swapping in a
    // behaviorally different interactive chat.
    expect(await findByText('legacy projection')).toBeTruthy();
  });

  it('offers Retry while the native session is still starting', async () => {
    mockFetch(bindingResponse({ state: 'starting', chatUrl: '' }));
    const { findByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active />,
    );

    const unavailable = await findByTestId('workflow-native-chat-unavailable');
    expect(unavailable.textContent).toContain('native_chat_session_starting');
    expect(await findByTestId('workflow-native-chat-retry')).toBeTruthy();
  });

  it('does not offer Retry for a runtime that never had a native binding', async () => {
    mockFetch(null, 404);
    const { findByText, queryByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    // A clean 404 is a stable historical state: no unavailable banner, no Retry,
    // just the read-only fallback projection.
    expect(await findByText('legacy projection')).toBeTruthy();
    expect(queryByTestId('workflow-native-chat-unavailable')).toBeNull();
    expect(queryByTestId('workflow-native-chat-retry')).toBeNull();
  });

  it('refetches the binding and mounts native chat when Retry succeeds', async () => {
    const fetchMock = vi
      .fn()
      // First attempt: upstream not reachable yet.
      .mockResolvedValueOnce({ ok: false, status: 500, json: async () => null } as unknown as Response)
      // Retry: the native binding is now available.
      .mockResolvedValue({ ok: true, status: 200, json: async () => bindingResponse() } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    const { findByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    const retry = await findByTestId('workflow-native-chat-retry');
    fireEvent.click(retry);

    const frame = await findByTestId('workflow-native-chat-frame');
    expect(frame.getAttribute('src')).toBe(CHAT_URL);
  });

  it('does not fetch while the chat tab is inactive', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active={false}>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
