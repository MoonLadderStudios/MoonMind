import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import '../styles/dashboard.css';

// Real-browser guardrail for the shared collection sidebar rail. The separator
// used to be a pseudo-element pinned `right: var(--…-scrollbar-width)` on the
// outer sidebar, and the rail was content-sized — so a short list produced a
// short divider, and the line could land inside or beside the native scrollbar
// depending on the browser, OS, and display scaling. jsdom cannot compute
// layout, border geometry, or overflow, so these invariants must be asserted in
// a real browser (Chromium and Firefox, since the old bug was scrollbar-
// implementation dependent).
//
// The rail's height is owned by the workspace via
// `--mm-collection-workspace-block-size` (the route shell writes it from the
// panel's top coordinate + visualViewport height). Here we set it inline to a
// deterministic value the way the shell would, then vary the row count.

const ROW_HEIGHT_PX = 64; // --workflow-list-body-row-height: 4rem @ 16px root.

function rowMarkup(index: number): string {
  return `
    <div role="row" class="workflow-workspace-sidebar-row-frame">
      <div role="cell" class="workflow-workspace-sidebar-cell">
        <a class="workflow-workspace-sidebar-row" href="/workflows/w${index}" data-active="false" data-pinned="false">
          <span class="workflow-workspace-sidebar-row-main">
            <span class="workflow-workspace-sidebar-title">Workflow ${index}</span>
          </span>
        </a>
      </div>
    </div>`;
}

function workspaceMarkup(rowCount: number, blockSizePx: number): string {
  const rows = Array.from({ length: rowCount }, (_, index) => rowMarkup(index + 1)).join('');
  return `
    <div
      class="collection-workspace collection-workspace--with-sidebar workflow-workspace-shell"
      style="--mm-collection-workspace-block-size: ${blockSizePx}px;"
      data-collection="workflow"
    >
      <aside class="collection-sidebar workflow-workspace-sidebar" aria-label="Workflow navigation">
        <div role="table" aria-label="Workflow list table slice" class="workflow-workspace-sidebar-table">
          <div role="rowgroup" class="workflow-workspace-sidebar-header">
            <div role="row" class="workflow-workspace-sidebar-header-row">
              <div role="columnheader" class="workflow-workspace-sidebar-header-cell">
                <span class="workflow-workspace-sidebar-header-title">Workflow</span>
              </div>
            </div>
          </div>
          <div role="rowgroup" class="workflow-workspace-sidebar-list" aria-label="Workflow navigation list">
            ${rows}
          </div>
        </div>
      </aside>
      <main class="collection-workspace__primary workflow-workspace-detail" aria-label="Workflow detail">
        <p>Detail content</p>
      </main>
    </div>`;
}

let container: HTMLElement;

function render(rowCount: number, blockSizePx: number): {
  shell: HTMLElement;
  sidebar: HTMLElement;
  list: HTMLElement;
} {
  container.innerHTML = workspaceMarkup(rowCount, blockSizePx);
  const shell = container.querySelector<HTMLElement>('.workflow-workspace-shell')!;
  const sidebar = container.querySelector<HTMLElement>('.collection-sidebar')!;
  const list = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-table')!;
  return { shell, sidebar, list };
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.style.margin = '0';
  document.body.appendChild(container);
});

afterEach(() => {
  container.remove();
});

describe('collection sidebar rail separator and height', () => {
  it('separates the rail with its own 1px right border, not a pseudo-element', () => {
    const { sidebar } = render(3, 600);
    const style = getComputedStyle(sidebar);

    // The separator is the rail's inline-end border (border-right in LTR).
    expect(style.borderRightWidth).toBe('1px');
    expect(style.borderRightStyle).toBe('solid');
    expect(style.borderRightColor).not.toBe('rgba(0, 0, 0, 0)');

    // The old pseudo-element divider draws nothing.
    const after = getComputedStyle(sidebar, '::after');
    expect(after.content).toBe('none');

    // The list is the scroll container.
    const list = container.querySelector<HTMLElement>('.workflow-workspace-sidebar-table')!;
    expect(getComputedStyle(list).overflowY).toBe('auto');
  });

  it('keeps a short list from overflowing while filling the full rail height', () => {
    const { sidebar, list } = render(3, 600);

    // The rail height comes from the workspace token, not the three rows.
    expect(Math.round(sidebar.getBoundingClientRect().height)).toBe(600);
    // Three rows do not overflow a 600px rail.
    expect(list.scrollHeight).toBeLessThanOrEqual(list.clientHeight + 1);
  });

  it('makes the rail bottom meet the visible workspace bottom', () => {
    // Emulate the route shell: fill from the shell's top to the viewport bottom.
    const probe = render(3, 0).shell;
    const shellTop = probe.getBoundingClientRect().top;
    const available = Math.round(window.innerHeight - shellTop);

    const { shell, sidebar } = render(3, available);
    // The workspace and its rail both reach the bottom of the viewport.
    expect(Math.abs(sidebar.getBoundingClientRect().bottom - window.innerHeight)).toBeLessThan(1.5);
    expect(Math.abs(shell.getBoundingClientRect().bottom - window.innerHeight)).toBeLessThan(1.5);
  });

  it('keeps the rail height and right edge fixed as the list overflows', () => {
    const short = render(3, 600);
    const shortHeight = short.sidebar.getBoundingClientRect().height;
    const shortRight = short.sidebar.getBoundingClientRect().right;

    const long = render(30, 600);
    const longRect = long.sidebar.getBoundingClientRect();

    // Thirty rows overflow the list...
    expect(long.list.scrollHeight).toBeGreaterThan(long.list.clientHeight + ROW_HEIGHT_PX);
    // ...but the rail stays the same height and the separator stays at the same
    // outer-right coordinate regardless of the row count.
    expect(Math.round(longRect.height)).toBe(Math.round(shortHeight));
    expect(Math.round(longRect.right)).toBe(Math.round(shortRight));
    expect(getComputedStyle(long.sidebar).borderRightWidth).toBe('1px');
  });

  it('keeps the sticky header at its natural height instead of stretching it to fill the rail', () => {
    // The rail owns a full-height block size and the table fills it, so a short
    // list leaves free vertical space. A grid without `align-content: start`
    // defaults to `stretch` and balloons the auto header row to absorb the
    // slack. The header height must not depend on how much slack there is: a
    // three-row list (lots of free space) and a thirty-row list (none) must
    // render the same header height.
    const short = render(3, 600);
    const shortHeader = short.list.querySelector<HTMLElement>('.workflow-workspace-sidebar-header')!;
    const shortHeaderHeight = shortHeader.getBoundingClientRect().height;

    const long = render(30, 600);
    const longHeader = long.list.querySelector<HTMLElement>('.workflow-workspace-sidebar-header')!;
    const longHeaderHeight = longHeader.getBoundingClientRect().height;

    // Same natural height regardless of list length...
    expect(Math.round(shortHeaderHeight)).toBe(Math.round(longHeaderHeight));
    // ...and nowhere near the full rail height (a stretched header would be
    // hundreds of px tall; the natural header row is ~44px + padding).
    expect(shortHeaderHeight).toBeLessThan(80);
  });

  it('does not move the separator or shrink the rail when the list is filtered down', () => {
    // Begin overflowing, then filter to a single entry (scrollbar disappears).
    const before = render(30, 600);
    const beforeRect = before.sidebar.getBoundingClientRect();
    const beforeRight = beforeRect.right;
    const beforeHeight = beforeRect.height;

    const after = render(1, 600);
    const afterRect = after.sidebar.getBoundingClientRect();

    // The line must not move and the rail must not shrink.
    expect(Math.round(afterRect.right)).toBe(Math.round(beforeRight));
    expect(Math.round(afterRect.height)).toBe(Math.round(beforeHeight));
    expect(getComputedStyle(after.sidebar).borderRightWidth).toBe('1px');
    // A single row leaves the list well under the rail height (no scrollbar).
    expect(after.list.scrollHeight).toBeLessThanOrEqual(after.list.clientHeight + 1);
  });
});
