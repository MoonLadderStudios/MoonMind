import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page } from 'vitest/browser';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient, screen, waitFor } from '../utils/test-utils';
import { SkillsPage } from '../entrypoints/skills';
import '../styles/dashboard.css';

// Real-browser guardrail for MoonLadderStudios/MoonMind#3346. The Skills catalog
// table must match the Workflow table's visual treatment: the body reveals the
// page background (transparent slab/rows, opaque sticky header), and the
// header/row dividers bleed to the collection-content edges. jsdom cannot
// resolve var()-based backgrounds or lay out the bleed geometry, so these
// invariants must be asserted against computed styles in a real browser with
// the production stylesheet applied.
//
// Viewports: 1440px exercises the desktop layout above the 1181px navigation
// breakpoint, 1280px covers an ordinary smaller desktop, and 375px covers the
// narrow-screen scroll container. Both themes are checked on the wide desktop.

const WIDE_DESKTOP = { width: 1440, height: 900 } as const;
const DESKTOP = { width: 1280, height: 800 } as const;
const NARROW = { width: 375, height: 812 } as const;

const LONG_SKILL_ID =
  'a-very-long-skill-identifier-that-keeps-going-so-narrow-layouts-must-scroll-to-reach-it';

const REPRESENTATIVE_SKILLS = [
  {
    id: 'speckit-orchestrate',
    label: 'Speckit Orchestrate',
    description: 'Plans and executes spec workflows end to end.',
    hasInputSchema: true,
    source: { kind: 'file', path: '/skills/speckit-orchestrate/SKILL.md' },
    markdown: '# Speckit\n\nExisting **worker** skill.',
  },
  {
    id: LONG_SKILL_ID,
    label: `Long name ${'x'.repeat(80)}`,
    description: `Long prose ${'y'.repeat(200)}`,
    markdown: '# Long skill\n\nWraps instead of stretching past the viewport.',
  },
  { id: 'pr-resolver', markdown: '# PR Resolver\n\nResolves pull requests.' },
];

function skillsPayload(): BootPayload {
  return {
    page: 'skills',
    apiBase: '/api',
    initialData: {
      dashboardConfig: { initialPath: '/skills' },
    },
  };
}

function renderCatalog() {
  return renderWithClient(
    <MemoryRouter initialEntries={['/skills']}>
      <Routes>
        <Route path="/skills" element={<SkillsPage payload={skillsPayload()} />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockSkillsFetch(items: unknown[] | null, failed = false): MockInstance {
  return vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith('/api/workflows/skills')) {
      if (failed) {
        return Promise.resolve({
          ok: false,
          status: 500,
          text: async () => 'boom',
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ items: { worker: [] }, legacyItems: items ?? [] }),
      } as Response);
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      text: async () => 'Unhandled fetch',
    } as Response);
  });
}

function alphaOf(color: string): number {
  const match = color.match(/rgba?\(([^)]+)\)/);
  if (!match) return Number.NaN;
  const components = match[1];
  if (!components) return Number.NaN;
  const parts = components.split(',').map((part) => part.trim());
  if (parts.length === 4) return Number(parts[3]);
  return 1;
}

function expectTransparent(color: string, what: string) {
  expect(`${what}: ${color}`).toBe(`${what}: rgba(0, 0, 0, 0)`);
}

let fetchSpy: MockInstance | null = null;
let cleanupRender: (() => void) | null = null;

function unmount() {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy?.mockRestore();
  fetchSpy = null;
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, '', '/skills');
});

afterEach(async () => {
  unmount();
  document.documentElement.classList.remove('dark');
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

describe('skills catalog visual parity (MoonLadderStudios/MoonMind#3346)', () => {
  it('renders the catalog body over the page surface with legible header text in light and dark themes', async () => {
    await page.viewport(WIDE_DESKTOP.width, WIDE_DESKTOP.height);
    fetchSpy = mockSkillsFetch(REPRESENTATIVE_SKILLS);
    const { unmount: stop } = renderCatalog();
    cleanupRender = stop;

    const table = await screen.findByRole('table', { name: 'Skills catalog' });
    const slab = table.closest('.data-table-slab') as HTMLElement | null;
    expect(slab).toBeTruthy();

    const tableStyle = getComputedStyle(table);
    // The table element itself stays transparent: the generic `table` rule
    // paints a panel fill, which would otherwise read as an opaque slab behind
    // the transparent rows, unlike the workflow `.queue-table-wrapper table`
    // treatment. Translucency comes from surface colors, never whole-table
    // opacity (which would also fade text and controls).
    expectTransparent(tableStyle.backgroundColor, 'table background');
    expect(tableStyle.opacity).toBe('1');
    expect(tableStyle.borderCollapse).toBe('separate');

    // Body rows reveal the page background in both parities of the zebra rule.
    const rows = table.querySelectorAll('tbody tr');
    expect(rows.length).toBeGreaterThanOrEqual(2);
    const firstRow = rows[0] as HTMLElement | undefined;
    const secondRow = rows[1] as HTMLElement | undefined;
    expect(firstRow).toBeTruthy();
    expect(secondRow).toBeTruthy();
    expectTransparent(getComputedStyle(firstRow as HTMLElement).backgroundColor, 'row background');
    expectTransparent(getComputedStyle(secondRow as HTMLElement).backgroundColor, 'alternating row background');

    // The sticky header keeps its panel fill and divider treatment while the
    // body runs transparent, and header text stays legible.
    const firstHeader = table.querySelector('thead th') as HTMLElement | null;
    expect(firstHeader).toBeTruthy();
    const headerStyle = getComputedStyle(firstHeader as HTMLElement);
    expect(headerStyle.position).toBe('sticky');
    expect(headerStyle.top).toBe('0px');
    expect(headerStyle.borderBottomWidth).toBe('0px');
    expect(alphaOf(headerStyle.backgroundColor)).toBeGreaterThan(0.9);
    expect(alphaOf(headerStyle.color)).toBeGreaterThan(0.7);

    // The slab is not a card: no border, transparent fill, desktop overflow
    // visible so the page-sticky header and edge-to-edge bleed are unchanged.
    const slabStyle = getComputedStyle(slab as HTMLElement);
    expect(slabStyle.borderTopWidth).toBe('0px');
    expectTransparent(slabStyle.backgroundColor, 'slab background');
    expect(slabStyle.overflowX).toBe('visible');

    // Normal cell padding is preserved after the bleed re-inset.
    const firstCell = table.querySelector('tbody td') as HTMLElement | null;
    expect(firstCell).toBeTruthy();
    expect(Number.parseFloat(getComputedStyle(firstCell as HTMLElement).paddingLeft)).toBeGreaterThan(0);

    // Row actions stay legible over the transparent body.
    const openLinks = await screen.findAllByRole('link', { name: /Open skill/ });
    expect(openLinks.length).toBeGreaterThan(0);
    const openLink = openLinks[0] as HTMLElement;
    expect(alphaOf(getComputedStyle(openLink).color)).toBeGreaterThan(0.7);
    openLink.focus();
    expect(document.activeElement).toBe(openLink);

    // The bled table reaches the collection primary-content edges rather than
    // ending inside extra side margins, with no horizontal document overflow.
    const primary = table.closest('.skills-catalog-primary') as HTMLElement | null;
    expect(primary).toBeTruthy();
    const tableRect = table.getBoundingClientRect();
    const primaryRect = (primary as HTMLElement).getBoundingClientRect();
    expect(Math.abs(tableRect.left - primaryRect.left)).toBeLessThanOrEqual(1.5);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 1);

    // Dark theme: the body still reveals the page surface while the header
    // keeps its fill and the text stays legible.
    document.documentElement.classList.add('dark');
    await waitFor(() => {
      expect(alphaOf(getComputedStyle(firstHeader as HTMLElement).backgroundColor)).toBeGreaterThan(0.9);
    });
    expectTransparent(getComputedStyle(table).backgroundColor, 'dark table background');
    expectTransparent(getComputedStyle(firstRow as HTMLElement).backgroundColor, 'dark row background');
    expect(alphaOf(getComputedStyle(firstHeader as HTMLElement).color)).toBeGreaterThan(0.7);
    expect(alphaOf(getComputedStyle(openLink).color)).toBeGreaterThan(0.7);
    document.documentElement.classList.remove('dark');
  });

  it('keeps narrow columns reachable without document overflow and preserves empty/error states', async () => {
    await page.viewport(NARROW.width, NARROW.height);
    fetchSpy = mockSkillsFetch(REPRESENTATIVE_SKILLS);
    const { unmount: stop } = renderCatalog();
    cleanupRender = stop;

    const table = await screen.findByRole('table', { name: 'Skills catalog' });
    const slab = table.closest('.data-table-slab') as HTMLElement | null;
    expect(slab).toBeTruthy();

    // Narrow viewports restore a horizontal scroll container (the fixed table
    // is wider than the page and `.dashboard-root` clips horizontal overflow),
    // while the document itself never scrolls sideways.
    await waitFor(() => {
      expect(getComputedStyle(slab as HTMLElement).overflowX).toBe('auto');
    });
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 1);

    // A long skill name renders and stays reachable through the slab scroller.
    const longSkill = await screen.findByText(`Long name ${'x'.repeat(80)}`);
    expect(longSkill).toBeTruthy();
    (slab as HTMLElement).scrollTo({ left: (slab as HTMLElement).scrollWidth });
    await waitFor(() => {
      const actionHeader = table.querySelector('th[data-column-key="actions"]') as HTMLElement | null;
      expect(actionHeader).toBeTruthy();
      const slabRect = (slab as HTMLElement).getBoundingClientRect();
      const actionRect = (actionHeader as HTMLElement).getBoundingClientRect();
      expect(actionRect.left).toBeLessThan(slabRect.right + 1);
    });
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 1);

    // Empty state: no rows, message visible, still no document overflow.
    unmount();
    fetchSpy = mockSkillsFetch([]);
    const second = renderCatalog();
    cleanupRender = second.unmount;
    expect(await screen.findByText('No skills available yet.')).toBeTruthy();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 1);

    // Error state: failure message with retry, still no document overflow.
    unmount();
    fetchSpy = mockSkillsFetch(null, true);
    const third = renderCatalog();
    cleanupRender = third.unmount;
    expect(await screen.findByText('Failed to load skills.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 1);
  });
});
