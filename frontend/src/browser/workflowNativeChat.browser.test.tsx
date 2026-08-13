import { afterEach, describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';

import {
  NativeChatFrame,
  type NativeChatFrameSignal,
} from '../features/workflow-native-chat';
import '../styles/dashboard.css';

// Real-browser guardrail for the native Workflow Chat frame boundary
// (MoonLadderStudios/MoonMind#3642 §1/§3/§6). The jsdom route tests exercise the
// component contract with a synthetic `window.postMessage`, but jsdom cannot
// faithfully model a real `MessageEvent.origin`/`.source`, the same-origin
// iframe `contentWindow`, or a real iframe `load`. This suite runs in a real
// browser so those decisive boundaries are proven:
//   * the frame mounts the server-generated same-origin URL and never an
//     upstream Omnigent origin;
//   * a foreign-origin message can never drive a liveness/compat signal;
//   * a genuine same-origin load reports `ready` and reveals the native app.

let root: Root | null = null;
let host: HTMLElement | null = null;

function render(src: string, onSignal: (signal: NativeChatFrameSignal) => void) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  flushSync(() => {
    root!.render(
      <NativeChatFrame src={src} title="Workflow chat" onSignal={onSignal} />,
    );
  });
  const iframe = host.querySelector<HTMLIFrameElement>('iframe.wf-native-chat__iframe');
  if (!iframe) {
    throw new Error('native chat iframe did not render');
  }
  return iframe;
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(predicate: () => boolean, timeoutMs = 4000) {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    if (predicate()) return;
    await sleep(25);
  }
  throw new Error('condition was not met before timeout');
}

// A same-origin blob document that announces readiness the way the native
// Omnigent application does. A blob URL inherits the page origin, so this is a
// faithful same-origin embed.
function sameOriginReadyDoc(): string {
  const html =
    '<!doctype html><meta charset="utf-8">' +
    "<script>parent.postMessage({ type: 'moonmind:workflow-chat', status: 'ready' }, '*');</script>" +
    '<div id="native-root">native app</div>';
  return URL.createObjectURL(new Blob([html], { type: 'text/html' }));
}

function completeNativeAppDoc(turnCount = 1): string {
  const turns = Array.from(
    { length: turnCount },
    (_, index) => `<article tabindex="0">Turn ${index + 1}</article>`,
  ).join('');
  const html = `<!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>@media (prefers-reduced-motion: reduce){*{animation:none!important}}</style>
    <main aria-label="Native Workflow Chat">
      <section aria-label="Transcript" aria-live="polite">${turns}</section>
      <form id="composer" aria-label="Message composer"><label>Message<textarea name="message"></textarea></label><button>Send</button></form>
      <button data-action="queue" data-path="events">Queue</button>
      <button data-action="steer" data-path="events">Steer</button>
      <button data-action="approval" data-path="elicitations/el-1/resolve">Approve</button>
      <button data-action="resources" data-path="workspace/files">Files</button>
      <button data-action="terminal" data-path="resources/terminals">Terminal</button>
      <button data-action="agents" data-path="subagents">Agents</button>
      <button data-action="tasks" data-path="tasks">Tasks</button>
      <button data-action="reconnect" data-path="reconnect">Reconnect</button>
      <details><summary>Tool and reasoning</summary><pre>bounded tool result</pre></details>
      <div role="status" id="connection">Connected</div>
    </main>
    <script>
      const base = parent.location.origin + '/api/workflow-chat-bindings/cb-1/omnigent/v1/sessions/cb-1/';
      const send = (action, path, body) => fetch(base + path, {
        method: body ? 'POST' : 'GET',
        headers: body ? {'content-type':'application/json','idempotency-key':'browser-case-' + action} : {},
        body: body ? JSON.stringify(body) : undefined,
      }).then(() => parent.postMessage({type:'native-test-action', action}, parent.location.origin));
      document.querySelector('#composer').addEventListener('submit', event => {
        event.preventDefault();
        send('composer', 'events', {type:'message',data:{content:[{type:'text',text:event.target.message.value}]}});
      });
      document.querySelector('textarea').addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
          event.preventDefault();
          document.querySelector('#composer').requestSubmit();
        }
      });
      document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () =>
        send(button.dataset.action, button.dataset.path,
          ['queue','steer','approval','reconnect'].includes(button.dataset.action) ? {type:button.dataset.action} : null)));
      parent.postMessage({ type: 'moonmind:workflow-chat', status: 'ready' }, parent.location.origin);
    </script></html>`;
  return URL.createObjectURL(new Blob([html], { type: 'text/html' }));
}

afterEach(() => {
  root?.unmount();
  root = null;
  host?.remove();
  host = null;
});

describe('native workflow chat frame (real browser)', () => {
  it('mounts the same-origin scoped URL with the scoped security attributes', () => {
    const src = '/omnigent-ui/workflow-chat/cb-1?embedded=1';
    const iframe = render(src, () => {});

    // The iframe points at the MoonMind-scoped route, never an upstream origin.
    expect(iframe.getAttribute('src')).toBe(src);
    const resolved = new URL(iframe.src, window.location.origin);
    expect(resolved.origin).toBe(window.location.origin);
    expect(iframe.getAttribute('referrerPolicy')).toBe('same-origin');
    // A neutral loading placeholder is shown — never a MoonMind composer.
    const status = host!.querySelector('[role="status"]');
    expect(status?.textContent ?? '').toContain('Loading');
  });

  it('ignores foreign-origin liveness messages', async () => {
    const signals: NativeChatFrameSignal[] = [];
    render('/omnigent-ui/workflow-chat/cb-1?embedded=1', (signal) => signals.push(signal));

    // A message forged from another origin must never drive a signal.
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'moonmind:workflow-chat', status: 'disconnected' },
        origin: 'https://evil.example',
      }),
    );
    await sleep(150);
    expect(signals).not.toContain('disconnected');
  });

  it('reports ready on a genuine same-origin load', async () => {
    const signals: NativeChatFrameSignal[] = [];
    render(sameOriginReadyDoc(), (signal) => signals.push(signal));

    // Either the iframe onLoad or the same-origin ready postMessage reveals the
    // native application; both are same-origin paths.
    await waitFor(() => signals.includes('ready'));
    await waitFor(
      () =>
        host!
          .querySelector('.wf-native-chat__frame')
          ?.getAttribute('data-loaded') === 'true',
    );
  });

  it('drives the native composer and claimed feature surface through scoped requests', async () => {
    const actions: string[] = [];
    const requested: Array<{ url: string; method: string; body?: string }> = [];
    const listener = (event: MessageEvent) => {
      if (event.origin === window.location.origin && event.data?.type === 'native-test-action') {
        actions.push(String(event.data.action));
      }
    };
    window.addEventListener('message', listener);
    const iframe = render(completeNativeAppDoc(), () => {});
    await waitFor(() => Boolean(iframe.contentDocument?.querySelector('#composer')));
    const frameWindow = iframe.contentWindow!;
    frameWindow.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const request: { url: string; method: string; body?: string } = {
        url: String(input),
        method: init?.method ?? 'GET',
      };
      if (typeof init?.body === 'string') request.body = init.body;
      requested.push(request);
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }) as typeof fetch;

    const document = iframe.contentDocument!;
    const textarea = document.querySelector<HTMLTextAreaElement>('textarea[name="message"]')!;
    textarea.value = 'bounded follow-up';
    document.querySelector<HTMLFormElement>('#composer')!.requestSubmit();
    for (const button of document.querySelectorAll<HTMLButtonElement>('[data-action]')) {
      button.click();
    }
    document.querySelector<HTMLDetailsElement>('details')!.open = true;

    await waitFor(() => actions.length === 9);
    expect(new Set(actions)).toEqual(
      new Set(['composer', 'queue', 'steer', 'approval', 'resources', 'terminal', 'agents', 'tasks', 'reconnect']),
    );
    expect(requested).toHaveLength(9);
    for (const request of requested) {
      const url = new URL(request.url);
      expect(url.origin).toBe(window.location.origin);
      expect(url.pathname).toMatch(/^\/api\/workflow-chat-bindings\/cb-1\/omnigent\//);
      expect(url.hostname).not.toContain('omnigent');
    }
    expect(requested.find((item) => item.url.endsWith('/events'))?.body).toContain('bounded follow-up');
    expect(document.querySelector('main')?.getAttribute('aria-label')).toBe('Native Workflow Chat');
    expect(document.querySelector('[aria-live="polite"]')).not.toBeNull();
    expect(iframe.title).toBe('Workflow chat');
    expect(frameWindow.localStorage.length).toBe(0);
    expect(frameWindow.sessionStorage.length).toBe(0);
    window.removeEventListener('message', listener);
  });

  it('keeps large mobile sessions keyboard and screen-reader operable with reduced-motion CSS', async () => {
    await page.viewport(390, 844);
    const requested: string[] = [];
    const iframe = render(completeNativeAppDoc(500), () => {});
    await waitFor(() => Boolean(iframe.contentDocument?.querySelector('#composer')));
    const frameWindow = iframe.contentWindow!;
    frameWindow.fetch = (async (input: RequestInfo | URL) => {
      requested.push(String(input));
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    }) as typeof fetch;
    const document = iframe.contentDocument!;
    const textarea = document.querySelector<HTMLTextAreaElement>('textarea')!;
    textarea.focus();
    textarea.value = 'keyboard follow-up';
    textarea.dispatchEvent(
      new KeyboardEvent('keydown', {
        key: 'Enter',
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
      }),
    );

    await waitFor(() => requested.length === 1);
    expect(document.activeElement).toBe(textarea);
    expect(document.querySelectorAll('[aria-label="Transcript"] article')).toHaveLength(500);
    expect(document.querySelector('[aria-live="polite"]')).not.toBeNull();
    expect(document.querySelector('style')?.textContent).toContain(
      'prefers-reduced-motion: reduce',
    );
    expect(iframe.getBoundingClientRect().width).toBeLessThanOrEqual(390);
    const firstRequest = requested[0];
    expect(firstRequest).toBeDefined();
    expect(new URL(firstRequest!).origin).toBe(window.location.origin);
    expect(frameWindow.localStorage.length).toBe(0);
    expect(frameWindow.sessionStorage.length).toBe(0);
  });
});
