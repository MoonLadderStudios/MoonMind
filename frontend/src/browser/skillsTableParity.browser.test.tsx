import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import { DashboardApp } from '../entrypoints/dashboard-app';
import { navigateTo } from '../lib/navigation';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';

import '../styles/dashboard.css';

// Real-browser guardrail for MoonLadderStudios/MoonMind#3346: the desktop Skills
// table must match the Workflow table's visual treatment (transparent body over
// the page surface, header/row dividers drawn with the shared list token and
// extended to the collection-content edges). jsdom cannot compute backgrounds,
// border colors, sticky positioning, or layout geometry, and string assertions
// against dashboard.css cannot prove the deployed appearance — so these
// invariants are asserted against computed styles and bounding boxes in a real
// browser (Chromium and Firefox via `npm run ui:test:browser`).
//
// Render the production dashboard router and pages with bounded read-model
// fixtures. Capture Workflow dividers before navigating to Skills in the same
// shell, viewport and theme; no copied table markup or synthetic padding.

const LONG_DESCRIPTION =
  'A very long description that must wrap inside the description column instead of stretching the table past the viewport. '.repeat(
    6,
  );

const skills = {
  legacyItems: [
    { id: 'pr-resolver', label: 'PR Resolver', description: 'Resolves pull requests.',
      hasInputSchema: true, source: { kind: 'file' }, markdown: '# PR Resolver' },
    { id: 'long-skill', label: 'A very long skill label that keeps going and going and going',
      description: LONG_DESCRIPTION, source: { kind: 'file' }, markdown: '# Long Skill' },
  ],
};
const uiInfo = {
  app: 'moonmind', apiBase: '/api',
  features: { skills: true, workflowList: true, workflowLiveUpdates: false },
  limits: {}, endpoints: {}, settingsPermissions: [],
  dashboardConfig: {
    pollIntervalsMs: { list: 60_000, detail: 60_000 },
    features: { temporalDashboard: { listEnabled: true } },
  },
};
let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;
let referenceDividers: Record<'light' | 'dark', string>;

let container: HTMLElement;

function queries() {
  const primary = container.querySelector<HTMLElement>('.skills-catalog-primary')!;
  const slab = container.querySelector<HTMLElement>('.skills-catalog-page .data-table-slab')!;
  const table = container.querySelector<HTMLElement>('.skills-catalog-page .data-table')!;
  const headerCells = Array.from(
    container.querySelectorAll<HTMLElement>('.skills-catalog-page .data-table thead th'),
  );
  const bodyRows = Array.from(
    container.querySelectorAll<HTMLElement>('.skills-catalog-page .data-table tbody tr'),
  );
  const bodyCells = Array.from(
    container.querySelectorAll<HTMLElement>('.skills-catalog-page .data-table tbody td'),
  );
  const theme = document.documentElement.classList.contains('dark') ? 'dark' : 'light';
  return { primary, slab, table, headerCells, bodyRows, bodyCells, referenceDivider: referenceDividers[theme] };
}

function bleedPx(primary: HTMLElement): number {
  const raw = getComputedStyle(primary).getPropertyValue('--skills-table-bleed').trim();
  const rootFont = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
  const match = raw.match(/^([\d.]+)rem$/);
  expect(raw, '--skills-table-bleed resolves to a rem length').toMatch(/^([\d.]+)rem$/);
  return Number.parseFloat(match![1]!) * rootFont;
}

function alphaOf(color: string): number | null {
  const match = color.match(
    /rgba?\(\s*[\d.]+\s*[, ]\s*[\d.]+\s*[, ]\s*[\d.]+(?:\s*[,/]\s*([\d.]+))?\s*\)/,
  );
  if (!match) return null;
  return match[1] === undefined ? 1 : Number.parseFloat(match[1]);
}

beforeEach(async () => {
  await page.viewport(1280, 800);
  document.body.style.margin = '0';
  document.documentElement.classList.remove('dark');
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, '', '/workflows');
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin);
    const json = (value: unknown) => new Response(JSON.stringify(value), {
      headers: { 'Content-Type': 'application/json' },
    });
    if (url.pathname === '/api/ui/info') return json(uiInfo);
    if (url.pathname.startsWith('/api/v1/provider-profiles')) return json([]);
    if (url.pathname === '/api/workflows/skills') return json(skills);
    if (url.pathname === '/api/executions') return json({ items: [{
      taskId: 'parity-workflow', workflowId: 'parity-workflow', title: 'Workflow reference',
      source: 'temporal', status: 'completed', state: 'completed',
      createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:01:00Z',
    }], nextPageToken: null });
    return json({ items: [] });
  });
  const rendered = renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);
  cleanupRender = rendered.unmount;
  container = rendered.container;
  await screen.findByRole('link', { name: 'Workflow reference' });
  const referenceCell = container.querySelector<HTMLElement>('.queue-table-wrapper tbody td')!;
  const light = getComputedStyle(referenceCell).borderBottomColor;
  document.documentElement.classList.add('dark');
  const dark = getComputedStyle(referenceCell).borderBottomColor;
  referenceDividers = { light, dark };
  document.documentElement.classList.remove('dark');
  navigateTo('/skills');
  await screen.findByText('PR Resolver');
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  document.documentElement.classList.remove('dark');
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, '', '/');
  await page.viewport(1280, 800);
});

describe('skills table visual parity (MoonMind#3346)', () => {
  it.each(['light', 'dark'] as const)(
    'runs body rows transparent over the page surface in the %s theme with legible text and no faded-table opacity',
    (theme) => {
      if (theme === 'dark') {
        document.documentElement.classList.add('dark');
      }
      const { slab, table, bodyRows, headerCells, bodyCells } = queries();

      // The slab is not a card: transparent fill, no border or shadow.
      expect(getComputedStyle(slab).backgroundColor).toBe('rgba(0, 0, 0, 0)');
      expect(getComputedStyle(slab).borderTopWidth).toBe('0px');

      // Both odd and even body rows run transparent (never `opacity` on the
      // whole table, which would also fade text and controls).
      for (const row of bodyRows) {
        expect(getComputedStyle(row).backgroundColor).toBe('rgba(0, 0, 0, 0)');
      }
      expect(getComputedStyle(table).opacity).toBe('1');

      // The sticky header keeps the translucent workflow treatment: neither
      // fully opaque nor fully transparent.
      for (const th of headerCells) {
        const alpha = alphaOf(getComputedStyle(th).backgroundColor);
        expect(alpha).not.toBeNull();
        expect(alpha!).toBeGreaterThan(0.9);
        expect(alpha!).toBeLessThan(1);
      }
      expect(getComputedStyle(headerCells[0]!).position).toBe('sticky');

      // Text stays legible: header and body cells render in the ink color,
      // not faded out with the transparent surfaces.
      expect(alphaOf(getComputedStyle(headerCells[0]!).color)).toBe(1);
      expect(alphaOf(getComputedStyle(bodyCells[0]!).color)).toBe(1);
    },
  );

  it.each(['light', 'dark'] as const)('matches the real Workflow row dividers and header rule in the %s theme', (theme) => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    const { bodyCells, headerCells, referenceDivider: expectedDivider } = queries();
    expect(expectedDivider).not.toBe('rgba(0, 0, 0, 0)');

    // Every Skills body cell divider resolves to the same computed color as
    // the Workflow list divider — not the generic 0.65 table rule.
    for (const cell of bodyCells) {
      expect(getComputedStyle(cell).borderBottomColor).toBe(expectedDivider);
    }

    // The header rule uses the same token via box-shadow (both computed in
    // the same engine, so the serialized color must match exactly).
    const headerShadow = getComputedStyle(headerCells[0]!).boxShadow;
    expect(headerShadow).not.toBe('none');
    expect(headerShadow.replace(/\s+/g, ' ')).toContain(expectedDivider.replace(/\s+/g, ' '));
  });

  it.each(['light', 'dark'] as const)('keeps readable row hover feedback in the %s theme', async (theme) => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    const { bodyRows, bodyCells, table } = queries();
    await userEvent.hover(bodyRows[0]!);
    await waitFor(() => expect(alphaOf(getComputedStyle(bodyRows[0]!).backgroundColor)).toBeGreaterThan(0));
    expect(getComputedStyle(table).opacity).toBe('1');
    expect(alphaOf(getComputedStyle(bodyCells[0]!).color)).toBe(1);
    await userEvent.hover(screen.getByRole('link', { name: 'MoonMind workflows' }));
  });

  it('extends header and divider edges to the collection-content boundary without document overflow', () => {
    const { primary, slab, table, headerCells, bodyCells } = queries();
    const bleed = bleedPx(primary);

    // The bleed token tracks the primary's own responsive padding.
    expect(Math.abs(Number.parseFloat(getComputedStyle(primary).paddingLeft) - bleed)).toBeLessThan(1);
    expect(Math.abs(Number.parseFloat(getComputedStyle(primary).paddingRight) - bleed)).toBeLessThan(1);

    // The bled slab spans the primary border-box edge to edge, so header and
    // row-divider edges land on the collection boundary.
    const primaryRect = primary.getBoundingClientRect();
    const slabRect = slab.getBoundingClientRect();
    expect(Math.abs(slabRect.left - primaryRect.left)).toBeLessThan(1.5);
    expect(Math.abs(slabRect.right - primaryRect.right)).toBeLessThan(1.5);

    // The header spans the full slab width: it must reach (or pass) both
    // slab edges rather than ending inside a side margin. One-sided
    // containment keeps this robust when the fixed-width catalog legitimately
    // overflows the slab (the narrow-viewport scroll container then owns it).
    const headerRow = headerCells[0]!.closest('tr')!.getBoundingClientRect();
    expect(headerRow.left).toBeLessThanOrEqual(slabRect.left + 1.5);
    expect(headerRow.right).toBeGreaterThanOrEqual(slabRect.right - 1.5);

    // Readable cell padding is preserved: edge cells re-inset text by the
    // same bleed amount.
    expect(Math.abs(Number.parseFloat(getComputedStyle(headerCells[0]!).paddingLeft) - bleed)).toBeLessThan(1);
    const lastHeader = headerCells[headerCells.length - 1]!;
    expect(Math.abs(Number.parseFloat(getComputedStyle(lastHeader).paddingRight) - bleed)).toBeLessThan(1);

    // Long prose wraps inside the description column instead of forcing
    // horizontal document overflow, even with a long skill name present.
    const descriptionCell = container.querySelector<HTMLElement>(
      'td[data-column-key="description"]',
    )!;
    expect(getComputedStyle(descriptionCell).overflowWrap).toBe('anywhere');
    expect(getComputedStyle(table).whiteSpace).toBe('normal');
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      document.documentElement.clientWidth + 1,
    );
    expect(bodyCells.length).toBeGreaterThan(0);
  });

  it('keeps long descriptions and controls usable with table-local scrolling on a narrow viewport', async () => {
    await page.viewport(390, 844);
    const { slab } = queries();
    await waitFor(() => expect(slab.clientWidth).toBeLessThan(390));

    const descriptionCell = container.querySelector<HTMLElement>(
      'td[data-column-key="description"]',
    )!;
    expect(getComputedStyle(descriptionCell).overflowWrap).toBe('anywhere');
    expect(slab.scrollWidth).toBeGreaterThan(slab.clientWidth);
    expect(getComputedStyle(slab).overflowX).toBe('auto');
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      document.documentElement.clientWidth + 1,
    );
    const open = within(slab).getByRole('link', { name: 'Open skill PR Resolver' });
    within(slab).getByRole('link', { name: /^PR Resolver/ }).focus();
    await userEvent.tab();
    expect(document.activeElement).toBe(open);
    expect(getComputedStyle(open).opacity).toBe('1');
    expect(getComputedStyle(open).outlineStyle).not.toBe('none');

  });
});
