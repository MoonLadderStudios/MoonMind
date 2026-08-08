import { afterEach, describe, expect, it } from 'vitest';
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
});
