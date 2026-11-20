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
      features: { temporalDashboard: { actionsEnabled: true, temporalWorkflowEditing: true } },
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
          actions: {
            canCancel: true,
            canRerun: true,
            canPause: false,
            disabledReasons: {
              canPause:
                'Pausing is unavailable while the workflow finalizes its published results and cannot accept operator signals.',
            },
          },
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
    const { container, unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;

    await screen.findAllByText('Example task');

    const heading = screen.getByRole('heading', { name: 'Workflows', level: 1 });
    expect(heading.getBoundingClientRect().height).toBeLessThanOrEqual(1);
    const tableHead = container.querySelector('thead')!;
    expect(tableHead.getBoundingClientRect().top).toBeCloseTo(
      container.firstElementChild!.getBoundingClientRect().top,
      0,
    );

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

describe('mobile workflow toolbar', () => {
  afterEach(() => {
    document.documentElement.style.fontSize = '';
  });

  const overlaps = (a: DOMRect, b: DOMRect) =>
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

  it.each([
    ['375px', 375, ''],
    ['320px', 320, ''],
    ['375px with 200% text', 375, '200%'],
  ] as const)('keeps Filters, sort and View options separate at %s', async (_label, width, fontSize) => {
    window.history.replaceState({}, '', '/workflows?stateNotIn=failed');
    document.documentElement.style.fontSize = fontSize;
    await page.viewport(width, MOBILE.height);
    const { container, unmount } = renderWithClient(
      <main className="dashboard-root">
        <WorkflowListPage payload={payload} />
      </main>,
    );
    cleanupRender = unmount;
    await waitFor(() => expect(container.querySelector('.queue-card')).not.toBeNull());

    const filters = screen.getByRole('button', { name: 'Filters' });
    const sortSelect = screen.getByRole('combobox', { name: 'Sort workflows' });
    const sort = sortSelect.closest('.workflow-list-sort-control') as HTMLElement;
    const viewOptions = screen.getByRole('button', { name: 'View options' });
    const controls = [filters, sort, viewOptions];
    const boxes = controls.map((control) => control.getBoundingClientRect());

    for (const [index, box] of boxes.entries()) {
      // Comfortable tap targets that stay on screen.
      expect(box.height, `control ${index} height`).toBeGreaterThanOrEqual(44);
      expect(box.width, `control ${index} width`).toBeGreaterThanOrEqual(44);
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.right).toBeLessThanOrEqual(window.innerWidth);
      expect(controls[index]!.scrollWidth).toBeLessThanOrEqual(controls[index]!.clientWidth + 1);
      for (const other of boxes.slice(index + 1)) {
        expect(overlaps(box, other), `control ${index} overlaps a later control`).toBe(false);
        // Controls on the same line keep a real gap between them.
        if (box.top < other.bottom && other.top < box.bottom) {
          expect(other.left - box.right).toBeGreaterThanOrEqual(6);
        }
      }
    }
    // The transparent native select covers the visible sort face, so a tap on
    // "Updated ↓" opens the platform list of sort choices.
    const selectBox = sortSelect.getBoundingClientRect();
    expect(selectBox.left).toBeCloseTo(boxes[1]!.left, 0);
    expect(selectBox.right).toBeCloseTo(boxes[1]!.right, 0);
    expect(selectBox.height).toBeCloseTo(boxes[1]!.height, 0);
    expect(sort.textContent).toContain('Updated ↓');

    // The row lines up with the card edges.
    const card = container.querySelector('.queue-card')!.getBoundingClientRect();
    expect(boxes[0]!.left).toBeCloseTo(card.left, 0);
    expect(boxes[2]!.right).toBeCloseTo(card.right, 0);

    // The overflow control is lighter than the two bordered main controls.
    const filtersStyle = getComputedStyle(filters);
    const overflowStyle = getComputedStyle(viewOptions);
    expect(filtersStyle.borderTopColor).not.toBe('rgba(0, 0, 0, 0)');
    expect(overflowStyle.borderTopColor).toBe('rgba(0, 0, 0, 0)');
    expect(overflowStyle.backgroundColor).toBe('rgba(0, 0, 0, 0)');

    const toolbar = filters.parentElement as HTMLElement;
    expect(toolbar.scrollWidth).toBeLessThanOrEqual(toolbar.clientWidth + 1);
    if (!fontSize) {
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
    }
    if (width === 375 && !fontSize) {
      // Two main controls and the overflow share one row at phone widths;
      // narrower or larger-text layouts may wrap instead.
      expect(new Set(boxes.map((box) => Math.round(box.top))).size).toBe(1);
    }

    // View options opens from the overflow without leaving the viewport.
    await page.getByRole('button', { name: 'View options' }).click();
    const popover = await screen.findByRole('dialog', { name: 'Workflow list view options' });
    const popoverBox = popover.getBoundingClientRect();
    expect(popoverBox.left).toBeGreaterThanOrEqual(0);
    expect(popoverBox.right).toBeLessThanOrEqual(window.innerWidth);
  });

  it('opens Filters as a bottom sheet on mobile and keeps the desktop drawer', async () => {
    await page.viewport(MOBILE.width, MOBILE.height);
    const { unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;
    await screen.findAllByText('Example task');

    await page.getByRole('button', { name: 'Filters' }).click();
    const sheet = await screen.findByRole('dialog', { name: 'Advanced filters' });
    // The fixed overlay spans the viewport excluding any scrollbar gutter. A
    // stable root gutter can reserve space even without overflow, and
    // clientWidth alone includes that reservation in Chromium.
    const viewport = () => ({
      width: Math.min(
        window.innerWidth,
        document.documentElement.clientWidth,
        document.documentElement.getBoundingClientRect().right,
      ),
      height: document.documentElement.clientHeight,
    });
    const sheetBox = sheet.getBoundingClientRect();
    expect(sheetBox.left).toBeCloseTo(0, 0);
    expect(sheetBox.right).toBeCloseTo(viewport().width, 0);
    expect(sheetBox.bottom).toBeCloseTo(viewport().height, 0);
    expect(sheetBox.top).toBeGreaterThanOrEqual(viewport().height * 0.14);
    for (const name of ['Clear all', 'Show results']) {
      const action = within(sheet).getByRole('button', { name }).getBoundingClientRect();
      expect(action.height).toBeGreaterThanOrEqual(44);
      expect(action.bottom).toBeLessThanOrEqual(viewport().height);
    }
    expect(sheet.scrollWidth).toBeLessThanOrEqual(sheet.clientWidth + 1);
    await page.getByRole('button', { name: 'Show results' }).click();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Advanced filters' })).toBeNull());

    await page.viewport(DESKTOP.width, DESKTOP.height);
    await page.getByRole('button', { name: 'Advanced filters' }).click();
    const drawer = await screen.findByRole('dialog', { name: 'Advanced filters' });
    const drawerBox = drawer.getBoundingClientRect();
    expect(drawerBox.top).toBeCloseTo(0, 0);
    expect(drawerBox.height).toBeCloseTo(viewport().height, 0);
    expect(drawerBox.right).toBeCloseTo(viewport().width, 0);
    expect(within(drawer).getByRole('button', { name: 'Apply filters' })).toBeTruthy();
    expect(within(drawer).getByRole('button', { name: 'Cancel filters' })).toBeTruthy();
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
    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Unavailable (2)' }));
    expect((await within(menu).findByRole('menuitem', { name: 'Remediate' })).getAttribute('aria-disabled')).toBe('true');
    expect(within(menu).getByRole('menuitem', { name: 'Pause' }).getAttribute('aria-disabled')).toBe('true');

    // Long reasons wrap inside the menu; it never scrolls sideways.
    expect(menu.scrollWidth).toBeLessThanOrEqual(menu.clientWidth);
    for (const item of within(menu).getAllByRole('menuitem')) {
      expect(item.scrollWidth).toBeLessThanOrEqual(item.clientWidth + 1);
    }

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
    expect(fetchSpy.mock.calls.some(([url]) => String(url).endsWith('/cancel'))).toBe(false);
    const dialog = await screen.findByRole('dialog', { name: 'Cancel workflow?' });
    expect(getComputedStyle(dialog).whiteSpace).toBe('normal');
    expect(dialog.getBoundingClientRect().right).toBeLessThanOrEqual(window.innerWidth);
    await page.getByRole('button', { name: 'Cancel workflow', exact: true }).click();
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
    // The data slab bleeds into the production shell's inline padding, so the
    // layout check renders inside the real `.dashboard-root` container.
    const { container, unmount } = renderWithClient(
      <main className="dashboard-root">
        <WorkflowListPage payload={payload} />
      </main>,
    );
    cleanupRender = unmount;

    await screen.findByRole('row', { name: /Long profile run/ });
    const headers = screen.getAllByRole('columnheader').map((header) => header.textContent || '');
    expect(headers.some((text) => text.includes('Provider Profile'))).toBe(true);
    for (const retired of ['Runtime', 'Harness', 'Backend', 'Host', 'Container']) {
      expect(headers.some((text) => text.trim().startsWith(retired))).toBe(false);
    }
    // Only the desktop table is mounted at this viewport.
    const table = within(screen.getByRole('table'));
    expect(table.getByText('Work · acct-a')).toBeTruthy();
    expect(table.getByText('Work · acct-b')).toBeTruthy();
    const longCell = table.getByText(longLabel).closest('td') as HTMLElement;
    expect(longCell.scrollWidth).toBeLessThanOrEqual(longCell.clientWidth + 1);
    const rootBox = (container.querySelector('.dashboard-root') as HTMLElement).getBoundingClientRect();
    const tableWrapper = container.querySelector('.queue-table-wrapper') as HTMLElement;
    expect(tableWrapper.scrollWidth).toBeLessThanOrEqual(tableWrapper.clientWidth + 1);
    for (const slabPart of container.querySelectorAll('.queue-table-wrapper, .workflow-list-results-footer')) {
      const box = slabPart.getBoundingClientRect();
      expect(box.left).toBeGreaterThanOrEqual(rootBox.left - 1);
      expect(box.right).toBeLessThanOrEqual(rootBox.right + 1);
    }
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

  it.each([
    { key: 'Enter', sequence: '{Enter}' },
    { key: 'Space', sequence: '[Space]' },
  ])('opens the Provider Profile column filter with $key and returns focus on Escape', async ({ sequence }) => {
    mockRows(rows);
    await page.viewport(DESKTOP.width, DESKTOP.height);
    const { unmount } = renderWithClient(<WorkflowListPage payload={payload} />);
    cleanupRender = unmount;

    await screen.findByRole('row', { name: /Long profile run/ });
    const filterButton = screen.getByRole('button', {
      name: 'Provider Profile filter. No filter applied.',
    });
    filterButton.focus();
    await waitFor(() => expect(document.activeElement).toBe(filterButton));
    await userEvent.keyboard(sequence);
    const popover = await screen.findByRole('dialog', { name: 'Provider Profile filter' });
    await waitFor(() => expect(popover.contains(document.activeElement)).toBe(true));
    expect(await within(popover).findByRole('checkbox', { name: 'Pending selection (1)' })).toBeTruthy();
    await userEvent.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Provider Profile filter' })).toBeNull();
    });
    await waitFor(() => expect(document.activeElement).toBe(filterButton));
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

  it.each([
    ['desktop', DESKTOP],
    ['mobile', MOBILE],
  ] as const)('stages mixed profile filters and loads comma-containing IDs on %s', async (surface, viewport) => {
    window.history.replaceState({}, '', '/workflows?providerProfileIn=acct-a&providerProfileNotIn=acct-b&providerProfileStateIn=pending&providerProfileStateNotIn=not_applicable');
    const profileUrls: string[] = [];
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/facets')) {
        if (!url.includes('facet=providerProfile')) return Promise.resolve({ ok: false } as Response);
        profileUrls.push(url);
        const continued = new URL(url, window.location.origin).searchParams.has('nextPageToken');
        return Promise.resolve({ ok: true, json: async () => ({
          facet: 'providerProfile',
          items: continued
            ? [{ value: 'account,primary', label: 'Later primary account', count: 1 }]
            : [{ value: 'acct-a', label: 'Work', count: 1 }, { value: 'acct-b', label: 'Work', count: 1 }],
          stateItems: [
            { value: 'pending', label: 'Pending selection', count: 1 },
            { value: 'not_recorded', label: 'Not recorded', count: 0 },
            { value: 'not_applicable', label: 'Not applicable', count: 0 },
          ],
          truncated: !continued,
          nextPageToken: continued ? null : 'profile-page-two',
          source: 'authoritative',
        }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ items: rows }) } as Response);
    });
    await page.viewport(viewport.width, viewport.height);
    const { unmount } = renderWithClient(
      <main className="dashboard-root"><WorkflowListPage payload={payload} /></main>,
    );
    cleanupRender = unmount;
    await screen.findAllByText('Long profile run');
    const openEditor = async () => {
      if (surface === 'desktop') await page.getByRole('button', { name: /^Provider Profile column filter:/ }).click();
      else await page.getByRole('button', { name: 'Filters', exact: true }).click();
      return screen.findByRole(surface === 'desktop' ? 'dialog' : 'region', { name: 'Provider Profile filter' });
    };
    const listParams = () => new URL(String(fetchSpy.mock.calls.filter(([url]) => String(url).startsWith('/api/executions?')).at(-1)?.[0]), window.location.origin).searchParams;
    let editor = await openEditor();
    await within(editor).findByRole('button', { name: 'Load more Provider Profiles' });
    expect(profileUrls).toHaveLength(1);
    await userEvent.selectOptions(within(editor).getByLabelText('Provider Profile filter mode'), 'exclude');
    expect((within(editor).getByRole('checkbox', { name: 'Not applicable (0)' }) as HTMLInputElement).checked).toBe(true);
    expect(within(editor).getByText('Work · acct-b (1)')).toBeTruthy();
    await userEvent.click(within(editor).getByRole('checkbox', { name: 'Not recorded (0)' }));
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole(surface === 'desktop' ? 'dialog' : 'region', { name: 'Provider Profile filter' })).toBeNull());
    expect(listParams().get('providerProfileStateNotIn')).toBe('not_applicable');
    editor = await openEditor();
    await userEvent.selectOptions(within(editor).getByLabelText('Provider Profile filter mode'), 'exclude');
    expect((within(editor).getByRole('checkbox', { name: 'Not recorded (0)' }) as HTMLInputElement).checked).toBe(false);
    await userEvent.selectOptions(within(editor).getByLabelText('Provider Profile filter mode'), 'include');
    await userEvent.click(within(editor).getByRole('button', { name: 'Load more Provider Profiles' }));
    await within(editor).findByRole('option', { name: 'Later primary account (1)' });
    expect(profileUrls.filter((url) => url.includes('nextPageToken='))).toHaveLength(1);
    await userEvent.selectOptions(within(editor).getByLabelText('Provider Profile filter value'), 'account,primary');
    expect(within(editor).getByText('Later primary account (1)')).toBeTruthy();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
    await page.getByRole('button', { name: surface === 'desktop' ? 'Apply Provider Profile filter' : 'Show results' }).click();
    await waitFor(() => expect(listParams().getAll('providerProfileIdIn')).toEqual(['account,primary', 'acct-a']));
    expect(listParams().getAll('providerProfileIdNotIn')).toEqual(['acct-b']);
    expect(listParams().get('providerProfileStateIn')).toBe('pending');
    expect(listParams().get('providerProfileStateNotIn')).toBe('not_applicable');
    expect(new URLSearchParams(window.location.search).getAll('providerProfileIdIn')).toEqual(['acct-a', 'account,primary']);
    editor = await openEditor();
    // Applying changed the facet query: keep the selected label without a
    // count until its continuation page is loaded again.
    expect(within(editor).getByText('Later primary account')).toBeTruthy();
    await userEvent.keyboard('{Escape}');
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
