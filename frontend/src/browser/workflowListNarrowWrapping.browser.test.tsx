import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { WorkflowListPage } from '../entrypoints/workflow-list';
import { renderWithClient, screen, waitFor } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser check that narrow tables and cards wrap only between words.
// `overflow-wrap: anywhere` on cells used to collapse column min-content
// widths, splitting ordinary words ("COMPLET ED", "Provid er Profile").

const DESKTOP = { width: 1280, height: 800 } as const;
const INTERACTIVE_SELECTOR =
  'a[href], button, input, select, textarea, summary, [role="button"], [tabindex]:not([tabindex="-1"])';

const payload: BootPayload = {
  page: 'workflows-workspace',
  apiBase: '/api',
  initialData: {
    dashboardConfig: {
      features: { temporalDashboard: { listEnabled: true, actionsEnabled: true } },
    },
  },
};

const rows = [
  {
    taskId: 'wf-review',
    workflowId: 'wf-review',
    source: 'temporal',
    title: 'Review the dashboard responsiveness changes before publishing',
    status: 'completed',
    state: 'completed',
    rawState: 'completed',
    repository: 'MoonLadderStudios/MoonMind',
    createdAt: '2026-03-28T00:00:00Z',
    providerProfile: {
      selectionState: 'recorded',
      profiles: [{ id: 'acct-claude', label: 'Claude Code', harness: 'claude-code' }],
      profileCount: 1,
    },
  },
  {
    taskId: 'wf-failed',
    workflowId: 'wf-failed',
    source: 'temporal',
    title: 'Investigate failing integration suite',
    status: 'failed',
    state: 'failed',
    rawState: 'failed',
    repository: 'MoonLadderStudios/MoonMind',
    createdAt: '2026-03-28T00:00:00Z',
    providerProfile: {
      selectionState: 'recorded',
      profiles: [{ id: 'acct-codex', label: 'Codex Production Account', harness: 'codex' }],
      profileCount: 1,
    },
  },
];

/** Words (whitespace-separated runs inside one text node) rendered across more than one line. */
function wordsSplitAcrossLines(root: Element): string[] {
  const split: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent || '';
    const parent = node.parentElement;
    if (!parent || parent.closest('.sr-only') || parent.getClientRects().length === 0) continue;
    for (const match of text.matchAll(/\S+/g)) {
      const range = document.createRange();
      range.setStart(node, match.index);
      range.setEnd(node, match.index + match[0].length);
      const lineTops = new Set(
        Array.from(range.getClientRects())
          .filter((rect) => rect.width > 0)
          .map((rect) => Math.round(rect.top)),
      );
      if (lineTops.size > 1) split.push(match[0]);
    }
  }
  return split;
}

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows');
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
    if (String(input).startsWith('/api/executions/facets?')) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ facet: 'status', items: [], source: 'authoritative', truncated: false }),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: async () => ({ items: rows }) } as Response);
  });
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

describe('workflow list wrapping at narrow widths', () => {
  it.each([
    ['1200px', 1200],
    ['800px', 800],
  ] as const)('keeps headers, pills, and ordinary words whole in the %s table', async (_label, width) => {
    await page.viewport(width, 800);
    const { container, unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;
    await screen.findByRole('row', { name: /Investigate failing integration suite/ });

    const table = container.querySelector('.queue-table-wrapper table') as HTMLElement;
    await waitFor(() => expect(table.getBoundingClientRect().width).toBeGreaterThan(0));

    // Columns hidden at this width render no boxes; only visible headers are checked.
    const headers = Array.from(table.querySelectorAll<HTMLElement>('.table-sort-button'))
      .filter((header) => header.getClientRects().length > 0);
    expect(headers.length).toBeGreaterThan(0);
    for (const header of headers) {
      expect(wordsSplitAcrossLines(header)).toEqual([]);
      // Header labels stay on one line.
      expect(new Set(Array.from(header.getClientRects()).map((rect) => Math.round(rect.top))).size).toBe(1);
    }

    const pills = Array.from(table.querySelectorAll<HTMLElement>('.status'));
    expect(pills.length).toBe(rows.length);
    for (const pill of pills) {
      expect(getComputedStyle(pill).whiteSpace).toBe('nowrap');
      expect(pill.scrollWidth).toBeLessThanOrEqual(pill.clientWidth + 1);
    }

    for (const cell of Array.from(table.querySelectorAll('tbody td'))) {
      expect(wordsSplitAcrossLines(cell)).toEqual([]);
    }

    // Repo is hidden at tablet widths and shown on wide tables.
    const repoHeader = table.querySelector<HTMLElement>('th[data-column="repository"]');
    expect(getComputedStyle(repoHeader as HTMLElement).display === 'none').toBe(width < 1200);
  });

  it('renders dense mobile cards with one detail link, a separate menu, and whole words', async () => {
    await page.viewport(375, 812);
    const { container, unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;

    await waitFor(() => {
      expect(container.querySelectorAll('.queue-card').length).toBe(rows.length);
    });

    for (const card of Array.from(container.querySelectorAll<HTMLElement>('.queue-card'))) {
      const links = Array.from(card.querySelectorAll('a[href]'));
      expect(links).toHaveLength(1);
      expect(card.querySelector('button[aria-label="More actions"]')).not.toBeNull();
      for (const control of Array.from(card.querySelectorAll(INTERACTIVE_SELECTOR))) {
        expect(control.parentElement?.closest(INTERACTIVE_SELECTOR)).toBeNull();
      }

      // The menu trigger and the title link are separate, non-overlapping targets.
      const title = links[0]!.getBoundingClientRect();
      const trigger = card.querySelector('button[aria-label="More actions"]')!.getBoundingClientRect();
      const overlaps =
        title.left < trigger.right && trigger.left < title.right && title.top < trigger.bottom && trigger.top < title.bottom;
      expect(overlaps).toBe(false);

      expect(wordsSplitAcrossLines(card)).toEqual([]);
      expect(card.scrollWidth).toBeLessThanOrEqual(card.clientWidth + 1);
      // Dense enough that more than two cards fit on a phone screen.
      expect(card.getBoundingClientRect().height).toBeLessThan(812 / 2.5);
    }
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
  });
});
