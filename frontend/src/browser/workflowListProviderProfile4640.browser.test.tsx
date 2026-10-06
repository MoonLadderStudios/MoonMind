import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { WorkflowListPage } from '../entrypoints/workflow-list';
import { renderWithClient, screen, waitFor } from '../utils/test-utils';
import '../styles/dashboard.css';

// MoonLadderStudios/MoonMind#4640: real-browser checks for the Provider
// Profile column against the production dashboard CSS — long and equal recorded
// names, narrow layouts, keyboard focus, empty/error states, and the
// current-page-only sort notice. jsdom cannot measure overflow or the 768px
// media query that swaps the table and card surfaces.

const DESKTOP = { width: 1280, height: 800 } as const;
const MOBILE = { width: 375, height: 812 } as const;

const LONG_NAME =
  'Anthropic · Organization-wide production account with an exceptionally long recorded display name';

const payload: BootPayload = {
  page: 'workflow-list',
  apiBase: '/api',
  initialData: {
    dashboardConfig: {
      features: { temporalDashboard: { listEnabled: true, actionsEnabled: true } },
    },
  },
};

const row = (workflowId: string, title: string, providerProfile: unknown) => ({
  taskId: workflowId,
  workflowId,
  source: 'temporal',
  title,
  status: 'completed',
  state: 'completed',
  rawState: 'completed',
  createdAt: '2026-03-28T00:00:00Z',
  providerProfile,
});

const PROFILE_ROWS = [
  row('wf-long', 'Long profile task', {
    selectionState: 'recorded',
    profiles: [{ id: 'profile-long-identifier-0001', label: LONG_NAME, harness: 'claude_code' }],
    profileCount: 1,
  }),
  row('wf-work-a', 'Work A task', {
    selectionState: 'recorded',
    profiles: [{ id: 'work-a', label: 'Work', harness: 'codex_cli' }],
    profileCount: 1,
  }),
  row('wf-work-b', 'Work B task', {
    selectionState: 'recorded',
    profiles: [{ id: 'work-b', label: 'Work', harness: 'codex_cli' }],
    profileCount: 1,
  }),
  row('wf-pending', 'Pending task', { selectionState: 'pending', profiles: [] }),
];

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;

function mockList(items: unknown[], { facetFails = true } = {}) {
  fetchSpy.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith('/api/executions/facets?')) {
      return Promise.resolve(
        facetFails
          ? ({ ok: false, statusText: 'Service Unavailable', json: async () => ({}) } as Response)
          : ({ ok: true, json: async () => ({ facet: 'providerProfile', items: [] }) } as Response),
      );
    }
    return Promise.resolve({ ok: true, json: async () => ({ items }) } as Response);
  });
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows');
  fetchSpy = vi.spyOn(window, 'fetch');
  mockList(PROFILE_ROWS);
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

function expectNoHorizontalPageOverflow() {
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 1);
}

describe('workflow list Provider Profile column', () => {
  it('wraps long and equal recorded names inside one compact desktop column', async () => {
    await page.viewport(DESKTOP.width, DESKTOP.height);
    const { container, unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;
    await screen.findAllByText('Long profile task');

    const headers = Array.from(container.querySelectorAll('table thead th')).map((th) => th.textContent || '');
    expect(headers.filter((text) => text.includes('Provider Profile'))).toHaveLength(1);
    expect(headers.some((text) => /Runtime|Harness|Backend|Host|Container/.test(text))).toBe(false);

    const cells = Array.from(
      container.querySelectorAll<HTMLElement>('table td .workflow-list-provider-profile'),
    );
    const names = cells.map((cell) => cell.querySelector('.workflow-list-provider-profile-name')?.textContent);
    expect(names).toContain('Work · work-a');
    expect(names).toContain('Work · work-b');
    expect(names).toContain('Pending selection');
    for (const cell of cells) {
      const td = cell.closest('td') as HTMLElement;
      // Long names wrap within the cell instead of widening the column.
      expect(td.scrollWidth).toBeLessThanOrEqual(td.clientWidth + 1);
    }
    expectNoHorizontalPageOverflow();
  });

  it('keeps long names readable on narrow mobile cards without page overflow', async () => {
    await page.viewport(MOBILE.width, MOBILE.height);
    const { container, unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;
    await screen.findAllByText('Long profile task');

    const card = Array.from(container.querySelectorAll<HTMLElement>('.queue-card')).find((element) =>
      element.textContent?.includes('Long profile task'),
    ) as HTMLElement;
    const terms = Array.from(card.querySelectorAll('dt')).map((term) => term.textContent);
    expect(terms).toContain('Provider Profile');
    expect(terms).not.toContain('Runtime');
    const value = card.querySelector('.workflow-list-provider-profile') as HTMLElement;
    expect(value.textContent).toContain(LONG_NAME);
    expect(value.textContent).toContain('Claude Code');
    expect(card.scrollWidth).toBeLessThanOrEqual(card.clientWidth + 1);
    expectNoHorizontalPageOverflow();
  });

  it('opens the column filter from the keyboard and keeps selected IDs when facets fail', async () => {
    await page.viewport(DESKTOP.width, DESKTOP.height);
    window.history.replaceState({}, '', '/workflows?providerProfileIn=work-a&limit=50');
    const { unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;
    await screen.findAllByText('Long profile task');

    const filterButton = screen.getByRole('button', { name: /^Provider Profile column filter: Work/ });
    filterButton.focus();
    await userEvent.keyboard('{Enter}');

    const dialog = await screen.findByRole('dialog', { name: 'Provider Profile filter' });
    await waitFor(() => {
      expect(dialog.contains(document.activeElement)).toBe(true);
    });
    expect(dialog.textContent).toContain('Facet values unavailable. Showing current page values only.');
    expect(dialog.textContent).toContain('Work · work-a');

    await userEvent.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Provider Profile filter' })).toBeNull();
    });
    expect(String(fetchSpy.mock.calls.at(-1)?.[0] ?? '')).not.toContain('/api/executions/wf-');
  });

  it('sorts the current page by recorded name and keeps the current-page notice', async () => {
    await page.viewport(DESKTOP.width, DESKTOP.height);
    const { container, unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;
    await screen.findAllByText('Long profile task');

    await page
      .getByRole('button', { name: /Provider Profile\. Not sorted\. Activate to sort the current page ascending\./ })
      .click();

    await waitFor(() => {
      const titles = Array.from(container.querySelectorAll('table a.workflow-list-row-title')).map(
        (link) => link.textContent,
      );
      expect(titles).toEqual(['Long profile task', 'Pending task', 'Work A task', 'Work B task']);
    });
    expect(screen.getByText('Sorting applies to the current page only.')).toBeTruthy();
    expect(window.location.search).not.toContain('sort=');
  });

  it('keeps the Provider Profile header and filter usable for empty results', async () => {
    await page.viewport(DESKTOP.width, DESKTOP.height);
    mockList([], { facetFails: false });
    const { unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;

    expect(await screen.findByText('No workflows found for the current filters.')).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: /Provider Profile/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Provider Profile filter. No filter applied.' })).toBeTruthy();
  });
});
