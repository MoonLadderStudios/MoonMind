import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient, screen, waitFor } from '../utils/test-utils';
import { MissionControlApp } from './mission-control-app';

vi.mock('@xterm/xterm', () => {
  class MockTerminal {
    cols = 80;
    rows = 24;
    private element: HTMLElement | null = null;
    constructor(_options?: unknown) {}
    loadAddon(_addon: unknown) {}
    open(element: HTMLElement) {
      this.element = element;
      element.setAttribute('data-testid', 'oauth-xterm');
    }
    write(data: string) {
      if (this.element) {
        this.element.textContent = `${this.element.textContent ?? ''}${data}`;
      }
    }
    writeln(data: string) {
      this.write(`${data}\n`);
    }
    onData(_callback: (data: string) => void) {
      return { dispose: vi.fn() };
    }
    dispose() {}
  }

  return { Terminal: MockTerminal };
});

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class MockFitAddon {
    fit() {}
  },
}));

describe('Mission Control shared entry', () => {
  let fetchSpy: MockInstance;
  const originalWebSocket = window.WebSocket;

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/secrets') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      if (url === '/api/v1/provider-profiles') {
        return Promise.resolve({
          ok: true,
          json: async () => [],
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: async () => 'Unhandled fetch',
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    window.WebSocket = originalWebSocket;
  });

  it('renders dashboard alerts and lazy-loads the requested page component', async () => {
    const payload: BootPayload = {
      page: 'tasks-home',
      apiBase: '/api',
      initialData: {
        layout: {
          dataWidePanel: true,
        },
      },
    };

    renderWithClient(<MissionControlApp payload={payload} />);

    expect(await screen.findByText('Hello from Tasks Home!')).toBeTruthy();
    expect(await screen.findByText(/First-Run Setup:/i)).toBeTruthy();
    await waitFor(() => {
      expect(document.querySelector('.panel--data-wide')).toBeTruthy();
    });
  });

  it('renders an explicit error state for unknown pages', async () => {
    renderWithClient(
      <MissionControlApp payload={{ page: 'not-a-page', apiBase: '/api' }} />,
    );

    expect(await screen.findByText(/Unknown Mission Control page:/i)).toBeTruthy();
    expect(screen.getByText('not-a-page')).toBeTruthy();
  });

  it('treats inherited object keys as unsupported pages', async () => {
    renderWithClient(
      <MissionControlApp payload={{ page: 'toString', apiBase: '/api' }} />,
    );

    expect(await screen.findByText(/Unknown Mission Control page:/i)).toBeTruthy();
    expect(screen.getByText('toString')).toBeTruthy();
  });

  it('renders the OAuth terminal page and attaches through the session bridge', async () => {
    const sentFrames: string[] = [];
    class MockWebSocket extends EventTarget {
      static readonly OPEN = 1;
      readonly OPEN = 1;
      readyState = 1;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(readonly url: string) {
        super();
        setTimeout(() => {
          this.onopen?.(new Event('open'));
          this.onmessage?.(new MessageEvent('message', { data: 'Ready for login' }));
        }, 0);
      }
      send(frame: string) {
        sentFrames.push(frame);
      }
      close() {
        this.onclose?.(new CloseEvent('close'));
      }
    }
    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/oauth-sessions/oas_terminal_ui/terminal/attach') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'oas_terminal_ui',
            terminal_session_id: 'term_oas_terminal_ui',
            terminal_bridge_id: 'br_oas_terminal_ui',
            websocket_url:
              '/api/v1/oauth-sessions/oas_terminal_ui/terminal/ws?token=once',
            attach_token: 'once',
          }),
        } as Response);
      }
      if (url === '/api/v1/secrets') {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      if (url === '/api/v1/provider-profiles') {
        return Promise.resolve({ ok: true, json: async () => [] } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: async () => 'Unhandled fetch',
      } as Response);
    });

    renderWithClient(
      <MissionControlApp
        payload={{
          page: 'oauth-terminal',
          apiBase: '/api',
          initialData: { sessionId: 'oas_terminal_ui' },
        }}
      />,
    );

    expect(await screen.findByText('Provider Login Terminal')).toBeTruthy();
    expect(await screen.findByText('Ready for login')).toBeTruthy();
    await waitFor(() => {
      expect(sentFrames.some((frame) => frame.includes('"heartbeat"'))).toBe(true);
    });
    expect(document.body.textContent).not.toContain('Docker exec');
  });
});
