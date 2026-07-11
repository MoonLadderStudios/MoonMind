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

// Every color stop in a computed background-image, with its alpha channel.
// rgb()/rgba() serializations both appear in Chromium output; a bare rgb()
// (no alpha) is fully opaque.
function gradientStopAlphas(backgroundImage: string): number[] {
  return Array.from(backgroundImage.matchAll(/rgba?\(([^)]+)\)/g)).map(([, channels]) => {
    const parts = channels.split(',').map((part) => part.trim());
    return parts.length === 4 ? Number.parseFloat(parts[3]!) : 1;
  });
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

      // The layers must carry the operator-approved MM-1036 palette: a
      // translucent accent halo (14%) under a translucent accent-2 core (34%)
      // on the approved -18deg path. This is the assertion that was missing
      // when the shimmer was "restored" with MM-1048's never-rendered brighter
      // treatment (30% halo, fully opaque whitened core): a gradient rendered
      // and moved, so the old guardrail passed while the look had changed
      // entirely. Computed colors, not authored CSS text, are compared so the
      // contract survives refactors of the custom-property plumbing.
      const expectedHalo = 'rgba(130, 72, 246, 0.14)';
      const expectedCore = theme === 'dark' ? 'rgba(125, 249, 255, 0.34)' : 'rgba(34, 211, 238, 0.34)';
      for (const layer of [before, after, letterAfter!]) {
        expect(layer.backgroundImage).toContain('-18deg');
        expect(layer.backgroundImage).toContain(expectedHalo);
        expect(layer.backgroundImage).toContain(expectedCore);
        // The sweep is a subtle translucent light field. An opaque (or
        // near-opaque) stop means the effect has been rebuilt brighter than
        // the approved design.
        for (const alpha of gradientStopAlphas(layer.backgroundImage)) {
          expect(alpha).toBeLessThanOrEqual(0.34);
        }
      }

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
