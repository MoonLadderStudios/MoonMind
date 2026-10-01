import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page } from 'vitest/browser';

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
