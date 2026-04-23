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

    loadAddon = vi.fn();
    open = vi.fn();
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
  it('forwards browser paste events to the terminal bridge', async () => {
    renderPage();
    const socket = await waitForSocket();
    const terminalSurface = document.querySelector('.oauth-terminal-xterm') as HTMLElement;
    const pasteEvent = new Event('paste', { bubbles: true, cancelable: true }) as ClipboardEvent;
    Object.defineProperty(pasteEvent, 'clipboardData', {
      value: {
        getData: vi.fn(() => 'oauth-code-123'),
      },
    });

    terminalSurface.dispatchEvent(pasteEvent);

    expect(pasteEvent.defaultPrevented).toBe(true);
    expect(socket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: 'oauth-code-123' }),
    );
  });

  it('pastes clipboard text with Ctrl+V', async () => {
    renderPage();
    const socket = await waitForSocket();
    vi.stubGlobal('navigator', {
      clipboard: {
        readText: vi.fn(async () => 'clipboard-token'),
      },
    });
    const terminalSurface = document.querySelector('.oauth-terminal-xterm') as HTMLElement;

    fireEvent.keyDown(terminalSurface, { key: 'v', ctrlKey: true });

    await waitFor(() =>
      expect(socket.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'input', data: 'clipboard-token' }),
      ),
    );
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

  it('pastes clipboard text from the toolbar button', async () => {
    renderPage();
    const socket = await waitForSocket();
    vi.stubGlobal('navigator', {
      clipboard: {
        readText: vi.fn(async () => 'button-paste'),
      },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Paste from clipboard' }));

    await waitFor(() =>
      expect(socket.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'input', data: 'button-paste' }),
      ),
    );
  });
});
