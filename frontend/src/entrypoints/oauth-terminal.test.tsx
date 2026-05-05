import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { OAuthTerminalPage } from './oauth-terminal';
import type { BootPayload } from '../boot/parseBootPayload';

type TerminalDataHandler = (data: string) => void;

const webSocketInstances: MockWebSocket[] = [];

type MockTerminal = {
  cols: number;
  rows: number;
  disposed: boolean;
  dataHandler: TerminalDataHandler | null;
  selection: string;
  loadAddon: ReturnType<typeof vi.fn>;
  open: ReturnType<typeof vi.fn>;
  write: ReturnType<typeof vi.fn>;
  writeln: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>;
  getSelection: ReturnType<typeof vi.fn>;
  onData: ReturnType<typeof vi.fn>;
  focus: ReturnType<typeof vi.fn>;
};

const { terminalInstances } = vi.hoisted(() => ({
  terminalInstances: [] as MockTerminal[],
}));

class MockWebSocket {
  static OPEN = 1;

  readyState = MockWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor(public url: string) {
    webSocketInstances.push(this);
  }
}

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    disposed = false;
    dataHandler: TerminalDataHandler | null = null;
    selection = '';
    helperTextarea: HTMLTextAreaElement | null = null;

    loadAddon = vi.fn();
    open = vi.fn((element: HTMLElement) => {
      const helperTextarea = document.createElement('textarea');
      helperTextarea.className = 'xterm-helper-textarea';
      element.appendChild(helperTextarea);
      this.helperTextarea = helperTextarea;
    });
    write = vi.fn();
    writeln = vi.fn();
    dispose = vi.fn(() => {
      this.disposed = true;
    });
    getSelection = vi.fn(() => this.selection);
    onData = vi.fn((handler: TerminalDataHandler) => {
      this.dataHandler = handler;
      return { dispose: vi.fn() };
    });
    focus = vi.fn(() => {
      this.helperTextarea?.focus();
    });

    constructor() {
      terminalInstances.push(this);
    }
  },
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    fit = vi.fn();
  },
}));

function renderPage() {
  const payload: BootPayload = {
    page: 'oauth-terminal',
    apiBase: '/api',
    initialData: { sessionId: 'session-1' },
  };
  render(<OAuthTerminalPage payload={payload} />);
}

function mockAttachFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL) => {
      const href = String(url);
      if (href.endsWith('/terminal/attach')) {
        return new Response(
          JSON.stringify({
            session_id: 'session-1',
            terminal_session_id: 'terminal-1',
            terminal_bridge_id: 'bridge-1',
            websocket_url: '/ws/oauth/terminal',
            attach_token: 'attach-token',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify({
          session_id: 'session-1',
          status: 'bridge_ready',
          terminal_session_id: 'terminal-1',
          terminal_bridge_id: 'bridge-1',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    }),
  );
}

async function waitForSocket() {
  await waitFor(() => expect(webSocketInstances).toHaveLength(1));
  const socket = webSocketInstances[0];
  if (!socket) {
    throw new Error('Expected OAuth terminal WebSocket');
  }
  socket.onopen?.();
  return socket;
}

function currentTerminal(): MockTerminal {
  const terminal = terminalInstances[0];
  if (!terminal) {
    throw new Error('Expected OAuth terminal instance');
  }
  return terminal;
}

beforeEach(() => {
  terminalInstances.length = 0;
  webSocketInstances.length = 0;
  mockAttachFetch();
  vi.stubGlobal('WebSocket', MockWebSocket);
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { origin: 'http://localhost', search: '' },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('OAuthTerminalPage clipboard behavior', () => {
  it('renders the session projection and finalizes through the shared OAuth endpoint', async () => {
    const storageSetItem = vi.spyOn(window.localStorage.__proto__, 'setItem');
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const href = String(url);
        if (href.endsWith('/terminal/attach')) {
          return new Response(
            JSON.stringify({
              session_id: 'session-1',
              terminal_session_id: 'terminal-1',
              terminal_bridge_id: 'bridge-1',
              websocket_url: '/ws/oauth/terminal',
              attach_token: 'attach-token',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (href.endsWith('/finalize')) {
          expect(init?.method).toBe('POST');
          expect(init?.body).toBeUndefined();
          return new Response(
            JSON.stringify({
              session_id: 'session-1',
              runtime_id: 'codex_cli',
              profile_id: 'codex-oauth',
              status: 'succeeded',
              profile_summary: {
                profile_id: 'codex-oauth',
                runtime_id: 'codex_cli',
                provider_id: 'openai',
                provider_label: 'OpenAI',
                credential_source: 'oauth_volume',
                runtime_materialization_mode: 'oauth_home',
                account_label: 'Codex Team',
                enabled: true,
                is_default: false,
                rate_limit_policy: 'backoff',
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        return new Response(
          JSON.stringify({
            session_id: 'session-1',
            runtime_id: 'codex_cli',
            profile_id: 'codex-oauth',
            status: 'awaiting_user',
            expires_at: '2026-05-05T22:00:00Z',
            terminal_session_id: 'terminal-1',
            terminal_bridge_id: 'bridge-1',
            session_transport: 'moonmind_pty_ws',
            profile_summary: {
              profile_id: 'codex-oauth',
              runtime_id: 'codex_cli',
              provider_id: 'openai',
              provider_label: 'OpenAI',
              credential_source: 'oauth_volume',
              runtime_materialization_mode: 'oauth_home',
              account_label: 'Codex Team',
              enabled: false,
              is_default: false,
              rate_limit_policy: 'backoff',
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }),
    );

    renderPage();
    await waitForSocket();

    expect(await screen.findByText('codex-oauth')).toBeTruthy();
    expect(screen.getByText('codex cli')).toBeTruthy();
    expect(screen.getByText('OpenAI')).toBeTruthy();
    expect(screen.getByText('2026-05-05T22:00:00Z')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Finalize Provider Profile' }));

    expect(await screen.findByText('Codex Team')).toBeTruthy();
    expect(screen.getAllByText('Succeeded').length).toBeGreaterThan(0);
    expect(storageSetItem).toHaveBeenCalledWith(
      'moonmind:provider-profile-updated',
      expect.stringContaining('codex-oauth'),
    );
  });

  it('shows recovery actions only for recoverable terminal sessions', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            session_id: 'session-1',
            runtime_id: 'codex_cli',
            profile_id: 'codex-oauth',
            status: 'failed',
            failure_reason: 'token=secret-value in /home/app/.codex/auth.json',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );

    renderPage();

    expect(await screen.findByText('token=[REDACTED] in [REDACTED_AUTH_PATH]')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Finalize Provider Profile' })).toBeNull();
  });

  it('forwards browser paste events from the xterm helper textarea to the terminal bridge once', async () => {
    renderPage();
    const socket = await waitForSocket();
    const terminalSurface = document.querySelector('.oauth-terminal-xterm') as HTMLElement;
    const helperTextarea = terminalSurface.querySelector('textarea') as HTMLTextAreaElement;
    const pasteEvent = new Event('paste', { bubbles: true, cancelable: true }) as ClipboardEvent;
    const inputFrame = JSON.stringify({ type: 'input', data: 'oauth-code-123' });
    Object.defineProperty(pasteEvent, 'clipboardData', {
      value: {
        getData: vi.fn(() => 'oauth-code-123'),
      },
    });

    helperTextarea.dispatchEvent(pasteEvent);

    expect(pasteEvent.defaultPrevented).toBe(true);
    expect(socket.send).toHaveBeenCalledWith(inputFrame);
    expect(socket.send.mock.calls.filter(([payload]) => payload === inputFrame)).toHaveLength(1);
  });

  it('leaves Ctrl+V available for the browser paste event', async () => {
    renderPage();
    await waitForSocket();
    const event = new KeyboardEvent('keydown', {
      key: 'v',
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    const terminalSurface = document.querySelector('.oauth-terminal-xterm') as HTMLElement;

    terminalSurface.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
  });

  it('copies selected terminal text with Ctrl+C instead of sending interrupt', async () => {
    renderPage();
    const socket = await waitForSocket();
    const terminal = currentTerminal();
    terminal.selection = 'selected text';
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText,
      },
    });
    const terminalSurface = document.querySelector('.oauth-terminal-xterm') as HTMLElement;

    fireEvent.keyDown(terminalSurface, { key: 'c', ctrlKey: true });

    expect(writeText).toHaveBeenCalledWith('selected text');
    expect(socket.send).not.toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: '\u0003' }),
    );
  });

  it('leaves Ctrl+C available for terminal input when no text is selected', async () => {
    renderPage();
    await waitForSocket();
    const terminal = currentTerminal();
    const event = new KeyboardEvent('keydown', {
      key: 'c',
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    const terminalSurface = document.querySelector('.oauth-terminal-xterm') as HTMLElement;

    terminalSurface.dispatchEvent(event);
    terminal.dataHandler?.('\u0003');

    expect(event.defaultPrevented).toBe(false);
  });

  it('loads clipboard text into the paste box from the toolbar button', async () => {
    renderPage();
    await waitForSocket();
    vi.stubGlobal('navigator', {
      clipboard: {
        readText: vi.fn(async () => 'button-paste'),
      },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Paste from clipboard' }));

    await waitFor(() => {
      expect(
        (screen.getByLabelText('Paste authentication code') as HTMLTextAreaElement).value,
      ).toBe('button-paste');
    });
  });

  it('sends manually pasted authentication code from the paste box to the terminal', async () => {
    renderPage();
    const socket = await waitForSocket();

    fireEvent.change(screen.getByLabelText('Paste authentication code'), {
      target: { value: 'manual-auth-code' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send to terminal' }));

    expect(socket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: 'manual-auth-code\n' }),
    );
    expect(
      (screen.getByLabelText('Paste authentication code') as HTMLTextAreaElement).value,
    ).toBe('');
  });

  it('focuses the xterm instance when the terminal surface is clicked', async () => {
    renderPage();
    await waitForSocket();
    const terminal = currentTerminal();
    const terminalSurface = document.querySelector('.oauth-terminal-xterm') as HTMLElement;

    fireEvent.click(terminalSurface);

    expect(terminal.focus).toHaveBeenCalledTimes(1);
  });

  it('keeps the manually pasted authentication code when the terminal socket is not open', async () => {
    renderPage();
    const socket = await waitForSocket();
    socket.readyState = 0;

    fireEvent.change(screen.getByLabelText('Paste authentication code'), {
      target: { value: 'manual-auth-code' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send to terminal' }));

    expect(socket.send).not.toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: 'manual-auth-code\n' }),
    );
    expect(
      (screen.getByLabelText('Paste authentication code') as HTMLTextAreaElement).value,
    ).toBe('manual-auth-code');
  });
});
