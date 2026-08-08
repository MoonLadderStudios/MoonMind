import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MockInstance } from 'vitest';

import { cleanup, fireEvent, renderWithClient, screen, waitFor } from '../../utils/test-utils';
import { WorkflowNativeChatRoute } from './WorkflowNativeChatRoute';
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

let fetchSpy: MockInstance;

function mockBinding(
  responder: () => { status?: number; body: unknown },
) {
  fetchSpy.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/chat-binding')) {
      const { status = 200, body } = responder();
      return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        clone() {
          return { json: async () => body } as Response;
        },
        json: async () => body,
      } as unknown as Response);
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) } as Response);
  });
}

function renderRoute(onNavigate = vi.fn()) {
  renderWithClient(
    <WorkflowNativeChatRoute
      apiBase="/api"
      workflowId="wf-1"
      routeWorkflowId="wf-1"
      search={new URLSearchParams('source=temporal')}
      workflowTitle="Ship the thing"
      runtimeLabel="Codex via Omnigent"
      pollIntervalMs={5000}
      onNavigate={onNavigate}
    />,
  );
  return onNavigate;
}

describe('WorkflowNativeChatRoute', () => {
  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch');
    fetchSpy.mockClear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('mounts the native application through the server scoped url without the legacy composer', async () => {
    mockBinding(() => ({ body: AVAILABLE }));
    renderRoute();

    const frame = await screen.findByTitle('Ship the thing — Omnigent chat');
    expect(frame.tagName).toBe('IFRAME');
    expect(frame.getAttribute('src')).toBe('/omnigent-ui/workflow-chat/cb-1?embedded=1');
    // No legacy custom composer/transcript is rendered on the native path.
    expect(document.querySelector('textarea')).toBeNull();
    expect(screen.queryByTestId('chat-session-blocks')).toBeNull();
    // Context bar exposes bounded workflow context + actions.
    expect(screen.getByText('Ship the thing')).toBeTruthy();
    expect(screen.getByText('Codex via Omnigent')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Back to Overview' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'View captured evidence' })).toBeTruthy();
  });

  it('never displays an upstream Omnigent url or provider session id', async () => {
    mockBinding(() => ({ body: AVAILABLE }));
    renderRoute();
    await screen.findByTitle('Ship the thing — Omnigent chat');
    const html = document.body.innerHTML;
    expect(html).not.toContain('run-1');
    expect(html).not.toContain('http://');
    expect(html).not.toContain('https://');
    // Open in Omnigent stays within the MoonMind-scoped full-page surface.
    const openLink = screen.getByRole('link', { name: 'Open in Omnigent' });
    expect(openLink.getAttribute('href')).toBe('/omnigent-ui/workflow-chat/cb-1');
  });

  it('routes Back to Overview through SPA navigation instead of a reload', async () => {
    mockBinding(() => ({ body: AVAILABLE }));
    const onNavigate = renderRoute();
    const backLink = await screen.findByRole('link', { name: 'Back to Overview' });
    fireEvent.click(backLink);
    expect(onNavigate).toHaveBeenCalledWith('overview', '/workflows/wf-1/overview?source=temporal');
  });

  it('shows a terminal read-only session with Continue in a new workflow', async () => {
    mockBinding(() => ({
      body: { ...AVAILABLE, state: 'ended', readOnly: true, capabilities: {} },
    }));
    renderRoute();
    await screen.findByTitle('Ship the thing — Omnigent chat');
    expect(screen.getByText(/session ended/i)).toBeTruthy();
    expect(
      screen.getByRole('link', { name: 'Continue in a new workflow' }),
    ).toBeTruthy();
  });

  it('renders an explicit unsupported-runtime state with no iframe', async () => {
    mockBinding(() => ({
      body: {
        ...AVAILABLE,
        chatBindingId: '',
        chatUrl: '',
        apiBase: '',
        state: 'unavailable',
        readOnly: true,
        unavailableReason: 'unsupported_runtime',
      },
    }));
    renderRoute();
    expect(
      await screen.findByText('Chat unavailable for this runtime'),
    ).toBeTruthy();
    expect(document.querySelector('iframe')).toBeNull();
    expect(screen.queryByRole('link', { name: 'Continue in a new workflow' })).toBeNull();
  });

  it('surfaces an unauthorized state and no session details', async () => {
    mockBinding(() => ({ status: 403, body: { detail: 'forbidden' } }));
    renderRoute();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/Not authorized/i);
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('surfaces the ambiguous-binding conflict', async () => {
    mockBinding(() => ({
      status: 409,
      body: { detail: { code: 'omnigent_chat_binding_ambiguous' } },
    }));
    renderRoute();
    expect(await screen.findByText('Multiple active sessions')).toBeTruthy();
  });

  it('falls back to an actionable state when the native app reports disconnected', async () => {
    mockBinding(() => ({ body: AVAILABLE }));
    renderRoute();
    const frame = (await screen.findByTitle(
      'Ship the thing — Omnigent chat',
    )) as HTMLIFrameElement;
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: window.location.origin,
        source: frame.contentWindow,
        data: { type: 'moonmind:workflow-chat', status: 'disconnected' },
      }),
    );
    expect(await screen.findByText('Chat disconnected')).toBeTruthy();
    // The full-page native escape hatch stays available as a durable fallback
    // (offered both in the context bar and the fallback panel).
    expect(
      screen.getAllByRole('link', { name: 'Open in Omnigent' }).length,
    ).toBeGreaterThan(0);
  });

  it('falls back when the native app reports an incompatible version', async () => {
    mockBinding(() => ({ body: AVAILABLE }));
    renderRoute();
    const frame = (await screen.findByTitle(
      'Ship the thing — Omnigent chat',
    )) as HTMLIFrameElement;
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: window.location.origin,
        source: frame.contentWindow,
        data: { type: 'moonmind:workflow-chat', status: 'incompatible' },
      }),
    );
    expect(await screen.findByText('Native chat could not load')).toBeTruthy();
  });

  it('ignores native-app messages from a foreign origin', async () => {
    mockBinding(() => ({ body: AVAILABLE }));
    renderRoute();
    const frame = (await screen.findByTitle(
      'Ship the thing — Omnigent chat',
    )) as HTMLIFrameElement;
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: 'https://evil.example',
        source: frame.contentWindow,
        data: { type: 'moonmind:workflow-chat', status: 'disconnected' },
      }),
    );
    // The frame stays mounted; a cross-origin message cannot force a fallback.
    expect(screen.getByTitle('Ship the thing — Omnigent chat')).toBeTruthy();
    expect(screen.queryByText('Chat disconnected')).toBeNull();
  });

  it('shows a polite starting placeholder without mounting the frame', async () => {
    mockBinding(() => ({
      body: {
        ...AVAILABLE,
        chatBindingId: '',
        chatUrl: '',
        apiBase: '',
        state: 'starting',
        readOnly: true,
      },
    }));
    renderRoute();
    await waitFor(() => expect(screen.getByText('Starting session')).toBeTruthy());
    expect(document.querySelector('iframe')).toBeNull();
    expect(screen.getByRole('status')).toBeTruthy();
  });
});
