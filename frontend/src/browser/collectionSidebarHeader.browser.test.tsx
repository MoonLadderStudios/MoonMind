import { afterEach, describe, expect, it } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { cleanup, render } from '@testing-library/react';
import { useState, type CSSProperties } from 'react';
import { CollectionSidebar, CollectionSidebarFilterHeader } from '../components/CollectionSidebar';
import '../styles/dashboard.css';

function Workspace({ count, pinned = false, filterHeader = true }: { count: number; pinned?: boolean; filterHeader?: boolean }) {
  const [filter, setFilter] = useState('');
  return <div className="collection-workspace collection-workspace--with-sidebar workflow-workspace-shell" style={{ '--mm-collection-workspace-block-size': '500px' } as CSSProperties}>
    <CollectionSidebar landmarkLabel="Workflow navigation" tableLabel="Workflow list" header="Workflow"
      filterLabel="Filter workflows" filterPlaceholder="Filter workflows"
      rows={Array.from({ length: count }, (_, i) => ({ id: `${i}`, href: `#workflow-${i}`, primaryText: `Workflow ${i}` }))}
      activeId={pinned ? 'current' : null} pinnedRow={pinned ? { id: 'current', href: '#current', primaryText: 'Pinned workflow' } : null}
      loadingCopy="Loading workflows" emptyCopy="No workflows match the current list filters."
      filteredEmptyCopy="No matching workflows" errorCopy="Unavailable" currentRowCopy="Current workflow"
      filterValue={filter} onFilterChange={setFilter}
      headerContent={filterHeader ? <CollectionSidebarFilterHeader filterText={filter} setFilterText={setFilter} copy={{
        columnHeader: 'Workflow', dialogLabel: 'Workflow filter', dialogTitle: 'Filter', fieldLabel: 'Workflow', placeholder: 'Filter workflows', inputLabel: 'Workflow filter value', triggerIdleLabel: 'Filter workflows', triggerActiveLabel: value => `Filter: ${value}`, resetLabel: 'Reset filter', applyLabel: 'Apply filter',
      }} /> : undefined}
    />
    <main className="collection-workspace__primary workflow-workspace-detail">Detail</main>
  </div>;
}

afterEach(() => { cleanup(); document.querySelector('[data-scrollbar-test]')?.remove(); });

describe('collection sidebar header reaches the divider', () => {
  for (const mode of ['reserved', 'non-reserving'] as const) {
    for (const count of [0, 3, 30]) {
      it(`${mode} scrollbars, ${count} rows`, async () => {
        if (mode === 'non-reserving') {
          const style = document.createElement('style');
          style.dataset.scrollbarTest = '';
          // Exercise overlay-equivalent geometry without relying on the host OS preference.
          style.textContent = '.workflow-workspace-sidebar-table, .workflow-workspace-sidebar-body { scrollbar-width: none; }';
          document.head.append(style);
        }
        const { container } = render(<Workspace count={count} />);
        const sidebar = container.querySelector<HTMLElement>('.collection-sidebar')!;
        const header = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-header')!;
        const scroller = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-body')
          ?? container.querySelector<HTMLElement>('.workflow-workspace-sidebar-table')!;
        const border = parseFloat(getComputedStyle(sidebar).borderRightWidth);
        const gap = sidebar.getBoundingClientRect().right - border - header.getBoundingClientRect().right;
        const gutter = scroller.offsetWidth - scroller.clientWidth;
        if (mode === 'non-reserving') expect(gutter).toBe(0);
        console.info(JSON.stringify({ mode, count, gutter, gap }));
        const title = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-title');
        const label = header.querySelector<HTMLElement>('.workflow-workspace-sidebar-header-title')!;
        if (title) expect(Math.abs(title.getBoundingClientRect().left - label.getBoundingClientRect().left)).toBeLessThan(1);
        await page.screenshot({ path: `../../../artifacts/browser-layout/sidebar-header-${mode}-${count}.png` });
        expect(Math.abs(gap)).toBeLessThan(1);
        expect(header.getBoundingClientRect().height).toBeLessThan(80);
        if (count === 30) {
          expect(scroller.scrollHeight).toBeGreaterThan(scroller.clientHeight);
          const before = header.getBoundingClientRect();
          scroller.scrollTop = 300;
          await new Promise(resolve => requestAnimationFrame(resolve));
          expect(header.getBoundingClientRect().top).toBe(before.top);
          expect(header.getBoundingClientRect().right).toBe(before.right);
        } else expect(scroller.scrollHeight).toBeLessThanOrEqual(scroller.clientHeight + 1);
      });
    }
  }

  it('keeps a pinned row directly below a taller inline-search header', async () => {
    const { container } = render(<Workspace count={30} pinned filterHeader={false} />);
    const header = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-header')!;
    const scroller = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-body')!;
    scroller.scrollTop = 300;
    await new Promise(resolve => requestAnimationFrame(resolve));
    const pinned = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-pinned-list')!;
    expect(Math.abs(pinned.getBoundingClientRect().top - header.getBoundingClientRect().bottom)).toBeLessThan(1);
    expect(header.contains(container.querySelector('input[type="search"]'))).toBe(true);
  });

  it('keeps the current row below the header while filtering and scrolling', async () => {
    const { container } = render(<Workspace count={30} pinned />);
    const header = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-header')!;
    const scroller = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-body')
      ?? container.querySelector<HTMLElement>('.workflow-workspace-sidebar-table')!;
    scroller.scrollTop = 300;
    await new Promise(resolve => requestAnimationFrame(resolve));
    const pinned = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-pinned-list')!;
    expect(Math.abs(pinned.getBoundingClientRect().top - header.getBoundingClientRect().bottom)).toBeLessThan(1);
    await userEvent.click(page.getByRole('button', { name: 'Filter workflows', exact: true }));
    await userEvent.fill(page.getByRole('searchbox', { name: 'Workflow filter value' }), 'not present');
    await userEvent.click(page.getByRole('button', { name: 'Apply filter' }));
    expect(container.textContent).toContain('No matching workflows');
    expect(container.textContent).toContain('Pinned workflow');
    expect(scroller.scrollHeight).toBeLessThanOrEqual(scroller.clientHeight + 1);
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Filter: not present');
  });
});
