import { afterEach, describe, expect, it } from 'vitest';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';

import { WorkflowLifecycleStatusPill } from '../components/ExecutionStatusPill';
import { resolveWorkflowDisplayStatus } from '../status/workflowStatus';
import '../styles/dashboard.css';

// Real-browser guardrail for the shimmer-sweep effect. The jsdom route tests
// verify the DOM contract (classes, data attributes, letter-wave markup), but
// they cannot detect an invalid computed gradient: a custom property declared
// where its inputs are undefined resolves to the guaranteed-invalid value and
// the pseudo-elements silently render background-image: none while their
// animation keeps running. That exact regression shipped once; these
// assertions are the missing guardrail.

let root: Root | null = null;
let host: HTMLElement | null = null;

function renderPill(status: string) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  flushSync(() => {
    root!.render(<WorkflowLifecycleStatusPill status={status} />);
  });
  const pill = host.querySelector<HTMLElement>('.status');
  if (!pill) {
    throw new Error('Status pill did not render');
  }
  return pill;
}

function shimmerLayerStyles(pill: HTMLElement) {
  const before = getComputedStyle(pill, '::before');
  const after = getComputedStyle(pill, '::after');
  const letterWave = pill.querySelector<HTMLElement>('.status-letter-wave');
  const letterAfter = letterWave ? getComputedStyle(letterWave, '::after') : null;
  return { before, after, letterAfter };
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

afterEach(() => {
  root?.unmount();
  root = null;
  host?.remove();
  host = null;
  document.documentElement.classList.remove('dark');
});

describe('status pill shimmer computed styles', () => {
  for (const theme of ['light', 'dark'] as const) {
    it(`renders a valid moving light field for an executing pill (${theme} mode)`, async () => {
      if (theme === 'dark') {
        document.documentElement.classList.add('dark');
      }

      const pill = renderPill('executing');
      expect(pill.dataset.effect).toBe('shimmer-sweep');
      expect(pill.dataset.state).toBe('executing');

      const { before, after, letterAfter } = shimmerLayerStyles(pill);

      // Fill, border, and text layers must all carry a real gradient image.
      expect(before.backgroundImage).not.toBe('none');
      expect(before.backgroundImage).toContain('linear-gradient');
      expect(after.backgroundImage).not.toBe('none');
      expect(after.backgroundImage).toContain('linear-gradient');
      expect(letterAfter).not.toBeNull();
      expect(letterAfter!.backgroundImage).not.toBe('none');
      expect(letterAfter!.backgroundImage).toContain('linear-gradient');

      // The shared animation drives all layers on the 2.6s cycle.
      expect(before.animationName).toBe('mm-status-pill-shimmer');
      expect(before.animationDuration).toBe('2.6s');
      expect(after.animationName).toBe('mm-status-pill-shimmer');
      expect(letterAfter!.animationName).toBe('mm-status-pill-shimmer');

      // The light field actually travels: the computed background position
      // must change over time.
      const initialPosition = before.backgroundPosition;
      await sleep(400);
      const laterPosition = getComputedStyle(pill, '::before').backgroundPosition;
      expect(laterPosition).not.toBe(initialPosition);

      // The label text remains visible on top of the effect.
      const letterWave = pill.querySelector<HTMLElement>('.status-letter-wave')!;
      const waveStyle = getComputedStyle(letterWave);
      expect(waveStyle.color).not.toBe('rgba(0, 0, 0, 0)');
      expect(letterWave.textContent).toBe('Executing');
    });
  }

  it('canonicalizes a raw running status to the executing shimmer treatment', () => {
    const displayStatus = resolveWorkflowDisplayStatus('running');
    expect(displayStatus).toBe('executing');
    const pill = renderPill(displayStatus!);
    expect(pill.dataset.effect).toBe('shimmer-sweep');
    expect(pill.dataset.state).toBe('executing');
    expect(pill.getAttribute('aria-label')).toBe('Executing');

    const { before } = shimmerLayerStyles(pill);
    expect(before.backgroundImage).not.toBe('none');
    expect(before.animationName).toBe('mm-status-pill-shimmer');
  });

  it('keeps completed pills static with no shimmer layers', () => {
    const pill = renderPill('completed');
    expect(pill.dataset.effect).toBeUndefined();

    const before = getComputedStyle(pill, '::before');
    expect(before.backgroundImage).toBe('none');
    expect(before.animationName).toBe('none');
  });
});
