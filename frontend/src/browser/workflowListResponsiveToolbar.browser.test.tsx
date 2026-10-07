import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { WorkflowListPage } from '../entrypoints/workflow-list';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser guardrail for the responsive workflow-list toolbar. The jsdom
// suite covers the same contract with a stubbed matchMedia, but only a real
// browser exercises the actual 768px media query against the CSS that styles
// both surfaces — the combination that regresses when mixed-build assets ship
// (a stale chunk rendering the mobile header row into a desktop layout).

const DESKTOP = { width: 1280, height: 800 } as const;
const MOBILE = { width: 375, height: 812 } as const;

const payload: BootPayload = {
  page: 'workflow-list',
  apiBase: '/api',
  initialData: {
    dashboardConfig: {
      features: { temporalDashboard: { listEnabled: true, actionsEnabled: true, temporalWorkflowEditing: true } },
    },
  },
};

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows');
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
    if (String(input) === '/api/executions/task-123?source=temporal') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          workflowId: 'task-123',
          runId: 'run-1',
          state: 'executing',
          actions: { canCancel: true, canRerun: true },
        }),
      } as Response);
    }
    return Promise.resolve({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-123',
            source: 'temporal',
            title: 'Example task',
            status: 'running',
            state: 'executing',
            rawState: 'executing',
            startedAt: '2026-03-28T00:00:01Z',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);
  });
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

describe('workflow list responsive toolbar', () => {
  it('keeps the mobile Filters/View-options header row off the desktop table and restores it on mobile', async () => {
    await page.viewport(DESKTOP.width, DESKTOP.height);
    const { unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;

    await screen.findAllByText('Example task');

    // Desktop: per-column filters own the table header, the "View options"
    // control collapses into the Actions header, and the standalone results
    // header row (Filters trigger + text "View options") must not render.
    expect(screen.queryByRole('button', { name: 'Filters' })).toBeNull();
    const viewOptions = screen.getByRole('button', { name: 'View options' });
    expect(viewOptions.closest('th, [role="columnheader"]')).not.toBeNull();
    expect(screen.getByRole('columnheader', { name: 'Actions' })).toBeTruthy();

    // Mobile: the real media query flips the layout and the header row returns.
    await page.viewport(MOBILE.width, MOBILE.height);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy();
    });
    const mobileViewOptions = screen.getByRole('button', { name: 'View options' });
    expect(mobileViewOptions.closest('th, [role="columnheader"]')).toBeNull();

    // And back: returning to desktop drops the header row again.
    await page.viewport(DESKTOP.width, DESKTOP.height);
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Filters' })).toBeNull();
    });
  });
});

describe('workflow list row actions', () => {
  it.each([
    ['desktop table', DESKTOP, '.queue-table-cell-actions'],
    ['mobile card', MOBILE, '.queue-card-actions'],
  ] as const)('keeps actions in the three-dot menu on the %s', async (_surface, viewport, selector) => {
    await page.viewport(viewport.width, viewport.height);
    const { container, unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;
    await screen.findAllByText('Example task');

    const row = container.querySelector(`${selector} .workflow-row-actions`) as HTMLElement;
    const trigger = within(row).getByRole('button', { name: 'More actions' });
    expect(within(row).getAllByRole('button')).toEqual([trigger]);
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/task-123?'))).toBe(false);

    await page.getByRole('button', { name: 'More actions' }).click();
    const menu = await within(row).findByRole('menu', { name: 'More actions' });
    await waitFor(() => {
      expect(within(menu).getByRole('menuitem', { name: 'Cancel' }).getAttribute('aria-disabled')).toBeNull();
      expect(within(menu).getByRole('menuitem', { name: 'Rerun' }).getAttribute('aria-disabled')).toBeNull();
    });
    expect(within(menu).getByRole('menuitem', { name: 'Remediate' }).getAttribute('aria-disabled')).toBe('true');

    const bounds = menu.getBoundingClientRect();
    expect(bounds.width).toBeGreaterThan(0);
    expect(bounds.left).toBeGreaterThanOrEqual(0);
    expect(bounds.right).toBeLessThanOrEqual(window.innerWidth);
    if (viewport === MOBILE) {
      const card = row.closest('.queue-card') as HTMLElement;
      const cardBounds = card.getBoundingClientRect();
      expect(bounds.left).toBeGreaterThanOrEqual(cardBounds.left);
      expect(bounds.right).toBeLessThanOrEqual(cardBounds.right);
      expect(bounds.bottom).toBeLessThanOrEqual(cardBounds.bottom);
    }

    const cancelItem = within(menu).getByRole('menuitem', { name: 'Cancel' });
    const cancelBounds = cancelItem.getBoundingClientRect();
    const hitTarget = document.elementFromPoint(
      cancelBounds.left + cancelBounds.width / 2,
      cancelBounds.top + cancelBounds.height / 2,
    );
    expect(cancelItem.contains(hitTarget)).toBe(true);

    await page.getByRole('menuitem', { name: 'Cancel', exact: true }).click();
    await waitFor(() => {
      const cancelCall = fetchSpy.mock.calls.find(([url]) => String(url) === '/api/executions/task-123/cancel');
      expect(cancelCall).toBeTruthy();
      expect(JSON.parse(String((cancelCall?.[1] as RequestInit).body))).toMatchObject({
        action: 'cancel',
        graceful: true,
      });
    });
    expect(within(row).queryByRole('menu')).toBeNull();
    expect(within(row).getAllByRole('button')).toEqual([trigger]);
  });
});

// MoonLadderStudios/MoonMind#4640: Provider Profile replaces the ordinary
// Runtime column. Real layout checks cover long and equal recorded names,
// absence states, narrow cards, keyboard access, and error/empty states.
describe('workflow list recorded Provider Profile', () => {
  const longLabel =
    'Anthropic Enterprise Production Account With An Exceptionally Long Recorded Display Name';
  const rows = [
    {
      taskId: 'wf-long',
      workflowId: 'wf-long',
      source: 'temporal',
      title: 'Long profile run',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      providerProfile: {
        selectionState: 'recorded',
        profiles: [{ id: 'acct-long', label: longLabel, harness: 'claude-code' }],
        profileCount: 1,
      },
    },
    {
      taskId: 'wf-equal-a',
      workflowId: 'wf-equal-a',
      source: 'temporal',
      title: 'Equal name A',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      providerProfile: {
        selectionState: 'recorded',
        profiles: [{ id: 'acct-a', label: 'Work' }],
        profileCount: 1,
      },
    },
    {
      taskId: 'wf-equal-b',
      workflowId: 'wf-equal-b',
      source: 'temporal',
      title: 'Equal name B',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      providerProfile: {
        selectionState: 'recorded',
        profiles: [{ id: 'acct-b', label: 'Work' }],
        profileCount: 1,
      },
    },
    {
      taskId: 'wf-pending',
      workflowId: 'wf-pending',
      source: 'temporal',
      title: 'Pending run',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      providerProfile: { selectionState: 'pending', profiles: [], profileCount: 0 },
    },
  ];

  const mockRows = (items: unknown[], options: { facetFails?: boolean } = {}) => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/executions/facets')) {
        return Promise.resolve(
          options.facetFails
            ? ({ ok: false, statusText: 'Service Unavailable', json: async () => ({}) } as Response)
            : ({
                ok: true,
                json: async () => ({
                  facet: 'providerProfile',
                  items: [
                    { value: 'acct-a', label: 'Work', count: 1 },
                    { value: 'acct-b', label: 'Work', count: 1 },
                  ],
                  stateItems: [
                    { value: 'pending', label: 'Pending selection', count: 1 },
                    { value: 'not_recorded', label: 'Not recorded', count: 0 },
                    { value: 'not_applicable', label: 'Not applicable', count: 0 },
                  ],
                  blankCount: 1,
                  source: 'authoritative',
                }),
              } as Response),
        );
      }
      return Promise.resolve({ ok: true, json: async () => ({ items }) } as Response);
    });
  };

  it('keeps one replacement column and wraps long or equal names on desktop', async () => {
    mockRows(rows);
    await page.viewport(DESKTOP.width, DESKTOP.height);
    const { unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;

    await screen.findByRole('row', { name: /Long profile run/ });
    const headers = screen.getAllByRole('columnheader').map((header) => header.textContent || '');
    expect(headers.some((text) => text.includes('Provider Profile'))).toBe(true);
    for (const retired of ['Runtime', 'Harness', 'Backend', 'Host', 'Container']) {
      expect(headers.some((text) => text.trim().startsWith(retired))).toBe(false);
    }
    expect(screen.getByText('Work · acct-a')).toBeTruthy();
    expect(screen.getByText('Work · acct-b')).toBeTruthy();
    const longCell = screen.getAllByText(longLabel)[0]!.closest('td') as HTMLElement;
    expect(longCell.scrollWidth).toBeLessThanOrEqual(longCell.clientWidth + 1);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);

    // Current-page sort by recorded display text; pending sorts after names.
    await page.getByRole('button', { name: /^Provider Profile\. Not sorted/ }).click();
    await waitFor(() => {
      const titles = Array.from(
        document.querySelectorAll('tbody tr .workflow-list-row-title'),
      ).map((link) => link.textContent);
      expect(titles).toEqual(['Long profile run', 'Equal name A', 'Equal name B', 'Pending run']);
    });
    expect(screen.getByText('Sorting applies to the current page only.')).toBeTruthy();
  });

  it('opens the Provider Profile column filter by keyboard and returns focus on Escape', async () => {
    mockRows(rows);
    await page.viewport(DESKTOP.width, DESKTOP.height);
    const { unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;

    await screen.findByRole('row', { name: /Long profile run/ });
    const filterButton = screen.getByRole('button', {
      name: 'Provider Profile filter. No filter applied.',
    });
    filterButton.focus();
    await userEvent.keyboard('{Enter}');
    const popover = await screen.findByRole('dialog', { name: 'Provider Profile filter' });
    await waitFor(() => expect(popover.contains(document.activeElement)).toBe(true));
    expect(await within(popover).findByRole('checkbox', { name: 'Pending selection (1)' })).toBeTruthy();
    await userEvent.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Provider Profile filter' })).toBeNull();
    });
  });

  it('keeps narrow cards readable and filters usable when facets fail', async () => {
    mockRows(rows, { facetFails: true });
    await page.viewport(MOBILE.width, MOBILE.height);
    const { container, unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;

    await waitFor(() => {
      expect(container.querySelectorAll('.queue-card').length).toBe(rows.length);
    });
    const longCard = Array.from(container.querySelectorAll('.queue-card')).find((card) =>
      card.textContent?.includes('Long profile run'),
    ) as HTMLElement;
    const terms = Array.from(longCard.querySelectorAll('dt')).map((term) => term.textContent);
    expect(terms).toContain('Provider Profile');
    expect(terms).not.toContain('Runtime');
    expect(longCard.scrollWidth).toBeLessThanOrEqual(longCard.clientWidth + 1);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);

    await page.getByRole('button', { name: 'Filters' }).click();
    const section = await screen.findByRole('region', { name: 'Provider Profile filter' });
    expect(
      await within(section).findByText('Facet values unavailable. Showing current page values only.'),
    ).toBeTruthy();
    expect(within(section).getByRole('option', { name: 'Work · acct-a' })).toBeTruthy();
  });

  it('shows the empty state with the Provider Profile header still available', async () => {
    mockRows([]);
    await page.viewport(DESKTOP.width, DESKTOP.height);
    const { unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;

    expect(await screen.findByText('No workflows found for the current filters.')).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: /Provider Profile/ })).toBeTruthy();
  });
});
