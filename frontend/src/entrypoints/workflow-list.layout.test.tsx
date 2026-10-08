import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { screen, within } from '@testing-library/react';

import { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { WorkflowListPage } from './workflow-list';
// Only these computed-style assertions load the dashboard stylesheet. With its
// ~1,800 rules applied, jsdom style lookups (including the visibility checks
// behind every getByRole name query) cost tens of milliseconds per element, so
// behavior tests stay in workflow-list.test.tsx without it.
import '../styles/dashboard.css';

describe('Workflows Entrypoint layout', () => {
  const mockPayload: BootPayload = {
    page: 'workflow-list',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.localStorage.clear();
    window.history.pushState({}, 'Test', '/workflows');
    fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-123',
            source: 'temporal',
            title: 'Example task',
            status: 'completed',
            state: 'succeeded',
            rawState: 'succeeded',
            startedAt: '2026-03-28T00:00:01Z',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
      }),
    } as Response);
  });

  it('renders pagination controls in the table footer', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            taskId: 'task-123',
            source: 'temporal',
            title: 'Example task',
            status: 'completed',
            state: 'succeeded',
            rawState: 'succeeded',
            createdAt: '2026-03-28T00:00:00Z',
          },
        ],
        nextPageToken: 'next-token',
        count: 21,
      }),
    } as Response);

    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    expect(await screen.findByText('1 - 1')).toBeTruthy();
    expect(screen.getByText('21 total entries')).toBeTruthy();
    const footer = document.querySelector('.workflow-list-results-footer');
    const liveBlock = footer?.querySelector('.workflow-list-footer-live');
    const paginationBlock = footer?.querySelector('.workflow-list-footer-pagination');
    const paginationSummary = footer?.querySelector('.workflow-list-footer-page-summary');
    expect(liveBlock?.querySelector('input[type="checkbox"]')).toBeNull();
    expect(liveBlock?.textContent).not.toMatch(/Live updates enabled\. Polling every \d+s/);
    expect(paginationBlock?.contains(screen.getByLabelText('Show'))).toBe(true);
    expect(getComputedStyle(paginationBlock as Element).flexWrap).toBe('wrap');
    expect(paginationSummary?.textContent).toContain('1 - 1');
    expect(paginationSummary?.textContent).toContain('21 total entries');
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeTruthy();
  });

  it('uses the data slab composition without a duplicate Workflows header title', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    await screen.findAllByText('Example task');

    const noticesDeck = document.querySelector<HTMLElement>('.workflow-list-notices-deck');
    const dataSlab = document.querySelector<HTMLElement>('.workflow-list-data-slab.panel--data');
    const resultsHeader = dataSlab?.querySelector<HTMLElement>('.workflow-list-results-header');
    const tableWrapper = dataSlab?.querySelector<HTMLElement>('.queue-table-wrapper[data-layout="table"]');
    const table = tableWrapper?.querySelector<HTMLElement>('table');
    const tableHead = tableWrapper?.querySelector<HTMLElement>('thead');
    const firstHeader = tableWrapper?.querySelector<HTMLElement>('th');

    expect(noticesDeck).toBeNull();
    expect(resultsHeader?.querySelector('.workflow-list-filter-trigger')).toBeTruthy();
    expect(resultsHeader?.querySelector('.page-title')).toBeNull();
    expect(resultsHeader?.textContent).not.toContain('Workflows');
    expect(tableHead?.textContent).toContain('Workflow');
    expect(screen.queryByRole('button', { name: /^Kind\./i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Workflow Type\./i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Entry\./i })).toBeNull();

    expect(dataSlab?.querySelector('.workflow-list-results-footer')?.textContent).not.toMatch(
      /Live updates enabled\. Polling every \d+s/,
    );
    expect(screen.queryByText('Showing all task executions.')).toBeNull();
    expect(dataSlab).toBeTruthy();
    expect(dataSlab?.querySelector('.workflow-list-results-footer')).toBeTruthy();
    const pageSizeSelect = screen.getByLabelText('Show');
    const pageSizeLabel = pageSizeSelect.closest('label');
    expect(pageSizeLabel?.classList.contains('queue-page-size-selector')).toBe(true);
    expect(pageSizeLabel?.classList.contains('queue-inline-filter')).toBe(false);
    expect(tableWrapper).toBeTruthy();
    // The slab intentionally allows overflow so the row actions popover
    // can extend below the table without being clipped.
    expect(getComputedStyle(dataSlab as HTMLElement).overflow).toBe('visible');
    expect(getComputedStyle(tableWrapper as HTMLElement).width).toBe(
      'calc(100% + (var(--workflow-list-slab-bleed-inline) * 2))',
    );
    expect(getComputedStyle(tableWrapper as HTMLElement).maxWidth).toBe('none');
    expect(getComputedStyle(tableWrapper as HTMLElement).overflowX).toBe('auto');
    expect(getComputedStyle(tableWrapper as HTMLElement).overflowY).toBe('visible');
    expect(getComputedStyle(tableWrapper as HTMLElement).scrollPaddingTop).not.toBe('auto');
    expect(getComputedStyle(table as HTMLElement).borderCollapse).toBe('separate');
    expect(getComputedStyle(table as HTMLElement).width).toBe('100%');
    expect(getComputedStyle(tableHead as HTMLElement).position).toBe('sticky');
    expect(getComputedStyle(tableHead as HTMLElement).top).toBe('0px');
    expect(getComputedStyle(firstHeader as HTMLElement).position).toBe('sticky');
    expect(getComputedStyle(firstHeader as HTMLElement).top).toBe('0px');
    expect(getComputedStyle(firstHeader as HTMLElement).borderBottomWidth).toBe('0px');
    expect(Number(getComputedStyle(firstHeader as HTMLElement).zIndex)).toBeGreaterThan(1);
  });

  it('keeps the workflow list surfaces to one data slab without an empty notices deck', async () => {
    renderWithClient(
      <section className="panel" aria-live="polite">
        <WorkflowListPage payload={mockPayload} />
      </section>,
    );

    await screen.findAllByText('Example task');

    const shellPanel = document.querySelector<HTMLElement>('.panel');
    const noticesDecks = document.querySelectorAll<HTMLElement>('.workflow-list-notices-deck');
    const dataSlabs = document.querySelectorAll<HTMLElement>('.workflow-list-data-slab.panel--data');

    expect(noticesDecks).toHaveLength(0);
    expect(dataSlabs).toHaveLength(1);

    const dataSlab = dataSlabs[0] as HTMLElement;
    const tableWrapper = dataSlab.querySelector<HTMLElement>('.queue-table-wrapper[data-layout="table"]');

    expect(tableWrapper).toBeTruthy();

    const shellPanelStyles = getComputedStyle(shellPanel as HTMLElement);
    expect(shellPanelStyles.borderTopWidth).toBe('0px');
    expect(shellPanelStyles.backgroundColor).toBe('rgba(0, 0, 0, 0)');
    expect(shellPanelStyles.boxShadow).toBe('none');
    expect(shellPanelStyles.paddingTop).toBe('0px');
    expect(shellPanelStyles.minHeight).toBe('0px');

    const dataSlabStyles = getComputedStyle(dataSlab);
    expect(dataSlabStyles.gap).toBe('0px');
    // The slab intentionally allows overflow so the row actions popover
    // can extend below the table without being clipped by the data slab.
    expect(dataSlabStyles.overflow).toBe('visible');
    expect(dataSlabStyles.paddingTop).toBe('0px');

    const tableWrapperStyles = getComputedStyle(tableWrapper as HTMLElement);
    expect(tableWrapperStyles.borderTopWidth).toBe('0px');
    expect(tableWrapperStyles.borderRadius).toBe('0px');
    expect(tableWrapperStyles.backgroundColor).toBe('rgba(0, 0, 0, 0)');
    expect(tableWrapperStyles.overflowX).toBe('auto');
    expect(tableWrapperStyles.overflowY).toBe('visible');
  });

  it('keeps mobile task cards constrained to the viewport width', async () => {
    renderWithClient(<WorkflowListPage payload={mockPayload} />);

    const detailsLink = await screen.findByRole('button', { name: 'View details' });
    const card = detailsLink.closest<HTMLElement>('.queue-card');
    const fields = card?.querySelector<HTMLElement>('.queue-card-fields');
    const fieldValue = fields?.querySelector<HTMLElement>('dd');

    expect(card).not.toBeNull();
    expect(fields).not.toBeNull();
    expect(fieldValue).not.toBeNull();
    expect(within(card as HTMLElement).queryByText('ID')).toBeNull();

    expect(getComputedStyle(card as HTMLElement).minWidth).toMatch(/^0(px)?$/);
    expect(getComputedStyle(card as HTMLElement).width).toBe('100%');
    expect(getComputedStyle(fields as HTMLElement).display).toBe('grid');
    expect(getComputedStyle(fieldValue as HTMLElement).minWidth).toMatch(/^0(px)?$/);
    expect(getComputedStyle(fieldValue as HTMLElement).overflowWrap).toBe('anywhere');
  });
});
