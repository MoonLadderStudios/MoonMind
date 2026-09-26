import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';

import { OAuthTerminalPage } from '../entrypoints/oauth-terminal';
import '../styles/dashboard.css';

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  await page.viewport(1280, 800);
});

it.each(['codex_cli', 'claude_code'])('supports the mobile %s terminal with native text controls', async (runtime) => {
  await page.viewport(390, 844);
  const sockets: Socket[] = [];
  class Socket {
    static OPEN = 1;
    readyState = 1;
    onopen: (() => void) | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onclose = null;
    onerror = null;
    send = vi.fn();
    close = vi.fn();
    constructor() { sockets.push(this); }
  }
  vi.stubGlobal('WebSocket', Socket);
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url.endsWith('/terminal/attach')
      ? { websocket_url: '/ws/oauth' }
      : { session_id: 'mobile', runtime_id: runtime, status: 'awaiting_user', terminal_session_id: 'terminal', terminal_bridge_id: 'bridge' },
  ), { status: 200 })));
  render(
    <main className="dashboard-root"><div className="dashboard-content"><section className="panel">
      <OAuthTerminalPage payload={{ page: 'oauth-terminal', apiBase: '/api', initialData: { sessionId: 'mobile' } }} />
    </section></div></main>,
  );
  await waitFor(() => expect(sockets).toHaveLength(1));
  const socket = sockets[0]!;
  socket.onopen?.();
  const url = `https://example.com/oauth?code=${'example'.repeat(20)}`;
  socket.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ type: 'output', data: `${url}\r\nPaste code here` }) }));
  // Use the real xterm buffer, including its mobile-width soft wraps.
  await waitFor(() => {
    fireEvent.click(screen.getByRole('button', { name: 'Select terminal text' }));
    expect((screen.getByLabelText('Selectable terminal text') as HTMLTextAreaElement).value).toContain(url);
  });
  const selectable = screen.getByLabelText('Selectable terminal text') as HTMLTextAreaElement;
  expect(selectable.readOnly).toBe(true);
  expect(selectable.selectionEnd - selectable.selectionStart).toBe(selectable.value.length);
  fireEvent.change(screen.getByLabelText('Paste authentication code'), { target: { value: 'example-code\n' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send to terminal' }));
  expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ type: 'input', data: 'example-code\r' }));

  const panel = document.querySelector('.panel')!;
  const surface = document.querySelector('.oauth-terminal-surface')!;
  expect(getComputedStyle(panel).borderTopWidth).toBe('0px');
  expect(getComputedStyle(panel).paddingLeft).toBe('0px');
  // The page can reserve a native scrollbar gutter; use its content width.
  expect(surface.getBoundingClientRect().width).toBe(document.body.getBoundingClientRect().width);
  expect(surface.getBoundingClientRect().left).toBe(0);
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390);
  expect(getComputedStyle(screen.getByLabelText('Paste authentication code')).fontSize).toBe('16px');

  await page.viewport(1280, 800);
  expect(getComputedStyle(panel).borderTopWidth).toBe('1px');
});
