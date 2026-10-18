import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import { DashboardApp } from '../entrypoints/dashboard-app';
import { updateDashboardPreferences } from '../utils/dashboardPreferences';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// MoonMind#2738: exercise the real router, table, sidebar and detail together.
// Only the execution read boundary is stubbed; no page or resolver is mocked.
const row = (workflowId: string, title: string) => ({
  taskId: workflowId, workflowId, title, source: 'temporal',
  workflowType: 'MoonMind.UserWorkflow', entry: 'user_workflow',
  status: 'completed', state: 'completed', rawState: 'completed',
  namespace: 'default', runId: `${workflowId}-run`, temporalRunId: `${workflowId}-run`,
  createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:01:00Z',
});
const selected = row('selected-workflow', 'Roundtrip selected');
const pageTwo = row('page-two-workflow', 'Roundtrip page two');
const pageThree = row('page-three-workflow', 'Roundtrip page three');
const uiInfo = {
  app: 'moonmind', apiBase: '/api', features: { workflowList: true, workflowLiveUpdates: false },
  limits: {}, endpoints: {}, settingsPermissions: [],
  dashboardConfig: {
    pollIntervalsMs: { list: 60_000, detail: 60_000, events: 60_000 },
    features: { temporalDashboard: { listEnabled: true, workspaceShellEnabled: true } },
  },
};
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
});

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;
let listRequests: URLSearchParams[];
let releaseHeldLists: (() => void) | null;

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, '',
    '/workflows/selected-workflow?source=temporal&titleContains=Roundtrip&limit=25&nextPageToken=page-two');
  listRequests = [];
  releaseHeldLists = null;
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin);
    if (url.pathname === '/api/ui/info') return json(uiInfo);
    if (url.pathname.startsWith('/api/v1/provider-profiles')) return json([]);
    if (url.pathname === '/api/executions') {
      listRequests.push(url.searchParams);
      return json({
        items: [url.searchParams.get('nextPageToken') === 'page-three' ? pageThree : pageTwo],
        nextPageToken: url.searchParams.get('nextPageToken') === 'page-three' ? null : 'page-three',
      });
    }
    if (url.pathname === '/api/executions/selected-workflow') return json(selected);
    if (url.pathname === '/api/executions/page-two-workflow') return json(pageTwo);
    if (url.pathname === '/api/executions/page-three-workflow') return json(pageThree);
    if (url.pathname.endsWith('/artifacts')) return json({ artifacts: [] });
    return json({ items: [] });
  });
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  releaseHeldLists?.();
  fetchSpy.mockRestore();
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, '', '/');
  await page.viewport(1280, 900);
});

function renderApp() {
  const { unmount } = renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);
  cleanupRender = unmount;
}

function expectContext(cursor: string) {
  const query = new URLSearchParams(window.location.search);
  expect(query.get('titleContains')).toBe('Roundtrip');
  expect(query.get('limit')).toBe('25');
  expect(query.get('nextPageToken')).toBe(cursor);
}

describe('Workflow list expansion round trip', () => {
  it('keeps the latest table page and filters and opens its first row when leaving the table', async () => {
    await page.viewport(1280, 900);
    renderApp();
    await screen.findByRole('complementary', { name: 'Workflow navigation' });
    await userEvent.click(screen.getByRole('radio', { name: 'Full screen table' }));
    await screen.findByRole('link', { name: 'Roundtrip page two' });
    expectContext('page-two');

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }));
    await screen.findByRole('link', { name: 'Roundtrip page three' });
    expectContext('page-three');
    await userEvent.click(screen.getByRole('button', { name: /Workflow\. Not sorted/ }));
    expect(screen.getByRole('columnheader', { name: /Workflow/ }).getAttribute('aria-sort')).toBe('ascending');
    screen.getByRole('radio', { name: 'Full screen table' }).focus();
    await userEvent.keyboard('{ArrowLeft}');
    // The workflow open before expanding is not on the visible page, so the
    // page's first row opens instead of a selection the table no longer shows.
    await waitFor(() => expect(window.location.pathname).toBe('/workflows/page-three-workflow'));
    expect(document.activeElement).toBe(screen.getByRole('radio', { name: 'Sidebar list' }));
    expectContext('page-three');
    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(await within(sidebar).findByRole('link', { name: /Roundtrip page three/ })).toBeTruthy();

    await userEvent.click(screen.getByRole('radio', { name: 'Full screen table' }));
    await screen.findByRole('link', { name: 'Roundtrip page three' });
    expectContext('page-three');
    expect(screen.getByRole('columnheader', { name: /Workflow/ }).getAttribute('aria-sort')).toBe('ascending');
    expect(window.location.search).not.toContain('sort');
    await userEvent.click(screen.getByRole('link', { name: 'Roundtrip page three' }));
    await waitFor(() => expect(window.location.pathname).toBe('/workflows/page-three-workflow'));
    expectContext('page-three');
    expect(listRequests.some((query) => query.get('nextPageToken') === 'page-three'
      && query.get('titleContains') === 'Roundtrip' && query.get('pageSize') === '25')).toBe(true);
    await page.viewport(390, 844);
    await waitFor(() => expect(screen.queryByRole('radiogroup', { name: 'Workflow list display' })).toBeNull());
    expect(window.location.pathname).toBe('/workflows/page-three-workflow');
  });

  it('reopens the remembered workflow when the visible page still shows it', async () => {
    await page.viewport(1280, 900);
    const visibleFirst = row('visible-first-workflow', 'Roundtrip visible first');
    const respond = fetchSpy.getMockImplementation()!;
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === '/api/executions') {
        listRequests.push(url.searchParams);
        return Promise.resolve(json({ items: [selected, visibleFirst], nextPageToken: null }));
      }
      return respond(input, init);
    });
    renderApp();
    await screen.findByRole('complementary', { name: 'Workflow navigation' });
    await userEvent.click(screen.getByRole('radio', { name: 'Full screen table' }));
    await screen.findByRole('link', { name: 'Roundtrip selected' });
    // The remembered workflow is visible but not the first row.
    expect(screen.getAllByRole('link', { name: /^Roundtrip / }).map((link) => link.textContent))
      .toEqual(['Roundtrip visible first', 'Roundtrip selected']);

    await userEvent.click(screen.getByRole('radio', { name: 'Sidebar list' }));
    await waitFor(() => expect(window.location.pathname).toBe('/workflows/selected-workflow'));
    expectContext('page-two');
  });

  it('ignores a pending first-workflow lookup after the list query changes', async () => {
    await page.viewport(1280, 900);
    window.history.replaceState({}, '', '/workflows?titleContains=Roundtrip&limit=25');
    const held: Array<() => void> = [];
    releaseHeldLists = () => held.splice(0).forEach((release) => release());
    const respond = fetchSpy.getMockImplementation()!;
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      // Hold every list read for the filtered query, so the table has not
      // settled and the shell must look up the first workflow itself.
      if (url.pathname === '/api/executions' && url.searchParams.get('titleContains') === 'Roundtrip') {
        return new Promise<Response>((resolve) => {
          held.push(() => resolve(json({ items: [pageTwo], nextPageToken: null })));
        });
      }
      return respond(input, init);
    });
    renderApp();
    await userEvent.click(await screen.findByRole('radio', { name: 'Sidebar list' }));
    await screen.findAllByText('Opening first workflow...');
    await userEvent.click(screen.getByRole('link', { name: 'MoonMind workflows' }));
    await waitFor(() => expect(new URLSearchParams(window.location.search).has('titleContains')).toBe(false));
    await screen.findByRole('link', { name: 'Roundtrip page two' });
    releaseHeldLists();
    await waitFor(() => expect(screen.queryByText('Opening first workflow...')).toBeNull());
    expect(window.location.pathname).toBe('/workflows');
    expect(new URLSearchParams(window.location.search).has('titleContains')).toBe(false);
    expect(screen.getByRole('radio', { name: 'Full screen table' }).getAttribute('aria-checked')).toBe('true');
  });

  it('keeps an empty table available when a remembered workflow is unavailable', async () => {
    await page.viewport(1280, 900);
    window.history.replaceState({}, '', '/workflows?titleContains=Roundtrip&limit=25');
    updateDashboardPreferences({ lastSelectedWorkflowId: 'missing-workflow' });
    const respond = fetchSpy.getMockImplementation()!;
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === '/api/executions/missing-workflow') return Promise.resolve(json({}, 404));
      if (url.pathname === '/api/executions') return Promise.resolve(json({ items: [], nextPageToken: null }));
      return respond(input, init);
    });
    renderApp();
    await screen.findByRole('radio', { name: 'Sidebar list' });
    await userEvent.click(screen.getByRole('radio', { name: 'Sidebar list' }));
    await screen.findByText('No workflow to open.');
    expect(window.location.pathname).toBe('/workflows');
    expect(new URLSearchParams(window.location.search).get('titleContains')).toBe('Roundtrip');
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
    expect(screen.getByRole('radio', { name: 'Full screen table' }).getAttribute('aria-checked')).toBe('true');
  });
});
