import { afterEach, describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { render, screen } from '@testing-library/react';

import { DataTable, type Column } from '../components/tables/DataTable';
import '../styles/dashboard.css';

// Real-browser guardrail for MoonLadderStudios/MoonMind#4559. Exercises the
// production DataTable responsive cards, the inventory toolbar/filter
// composition, the provider table-to-card transformation, and the shared
// fieldset/legend + tier-editor contract at narrow widths with synthetic
// credential-free fixtures. Geometry (not DOM presence) proves the four
// reported patterns: no sideways page panning and no clipped ordinary
// field/text/action.

interface Row {
  id: string;
  name: string;
  status: string;
  summary: string;
}

const LONG_ID = 'agent-profile-with-a-very-long-unbroken-identity-that-must-wrap-on-mobile-0123456789';
const LONG_SUMMARY =
  'A very long summary that must wrap naturally inside the mobile record instead of stretching the page. '.repeat(
    4,
  );

const rows: Row[] = [
  { id: LONG_ID, name: 'Long agent identity', status: 'Available', summary: LONG_SUMMARY },
  { id: 'short-id', name: 'Short', status: 'Available', summary: 'Short summary.' },
];

const columns: Column<Row>[] = [
  {
    key: 'identity',
    header: 'Identity',
    render: (row) => (
      <span className="omnigent-inventory__identity">
        <strong>{row.name}</strong>
        <small>{row.id}</small>
      </span>
    ),
  },
  { key: 'status', header: 'Status', render: (row) => row.status },
  { key: 'summary', header: 'Summary', render: (row) => row.summary },
];

function renderInventoryComposition() {
  return render(
    <div className="dashboard-content">
      <div className="omnigent-inventory">
        <header>
          <p className="eyebrow">Omnigent</p>
          <h1>Agents</h1>
          <p>Available agent identities and runtime status.</p>
        </header>
        <section aria-label="Agents inventory">
          <div className="omnigent-inventory__toolbar">
            <h2>Agents inventory</h2>
            <button type="button">Refresh</button>
          </div>
          <label>
            <span>Filter agents</span>
            <input type="search" defaultValue={LONG_ID} aria-label="Filter agents" />
          </label>
          <div className="omnigent-inventory__table-wrap">
            <DataTable<Row>
              data={rows}
              ariaLabel="Agents inventory"
              responsive
              getRowKey={(row) => row.id}
              columns={columns}
              rowActions={() => (
                <span className="omnigent-inventory__row-actions">
                  <button type="button">Inspect</button>
                  <button type="button">Activate / rollback with a very long action label</button>
                </span>
              )}
              rowActionsHeader="Actions"
            />
          </div>
        </section>
      </div>
    </div>,
  );
}

function renderProviderComposition() {
  const host = document.createElement('div');
  host.className = 'dashboard-content';
  host.innerHTML = `
    <div class="provider-profiles-table-wrap">
      <table class="provider-profiles-table" role="table">
        <thead><tr>
          <th scope="col">Profile</th><th scope="col">Runtime</th><th scope="col">Status</th><th scope="col">Actions</th>
        </tr></thead>
        <tbody><tr>
          <td data-label="Profile"><div>${LONG_ID}</div><div class="text-xs">Runtime default</div></td>
          <td data-label="Runtime">codex_cli</td>
          <td data-label="Status">Connected with a very long readiness explanation that must wrap</td>
          <td data-label="Actions"><div class="flex flex-wrap"><button type="button">Edit provider profile</button><button type="button">Validate credentials</button><button type="button">Rotate secret material</button><button type="button">Remove profile</button></div></td>
        </tr></tbody>
      </table>
    </div>
    <form class="space-y-6 provider-profile-form">
      <fieldset>
        <legend>Identity &mdash; required</legend>
        <label>Profile ID<input value="${LONG_ID}" /></label>
      </fieldset>
      <fieldset class="provider-tier-editor">
        <legend>Model &amp; effort tiers</legend>
        <ol aria-label="Model and effort tiers">
          <li>
            <fieldset>
              <legend><span>Tier 1 · long tier label that must wrap</span></legend>
              <div aria-label="Tier 1 actions">
                <button type="button">Duplicate tier</button>
                <button type="button">Remove tier</button>
              </div>
              <label>Model<select><option>A very long model option that must fit the available width</option></select></label>
            </fieldset>
          </li>
        </ol>
      </fieldset>
    </form>
  `;
  document.body.appendChild(host);
  return host;
}

async function assertNoPageOverflow(viewport: { width: number; height: number }, label: string) {
  await page.viewport(viewport.width, viewport.height);
  // Let the real media queries + container queries settle.
  await new Promise((resolve) => setTimeout(resolve, 50));
  const tolerance = 2;
  expect(
    document.documentElement.scrollWidth,
    `${label}: page must not pan sideways at ${viewport.width}px`,
  ).toBeLessThanOrEqual(window.innerWidth + tolerance);
  expect(
    document.body.scrollWidth,
    `${label}: body must not pan sideways at ${viewport.width}px`,
  ).toBeLessThanOrEqual(window.innerWidth + tolerance);
}

afterEach(async () => {
  document.body.innerHTML = '';
  await page.viewport(1280, 800);
});

describe('mobile overflow and cramped cards/forms (MoonMind#4559)', () => {
  it('keeps inventory records, filter, and actions inside 320px without sideways panning', async () => {
    const { unmount } = renderInventoryComposition();
    try {
      for (const viewport of [
        { width: 320, height: 568 },
        { width: 390, height: 844 },
        { width: 768, height: 800 },
      ]) {
        await assertNoPageOverflow(viewport, 'inventory');
        const filter = screen.getByLabelText('Filter agents');
        const filterRect = filter.getBoundingClientRect();
        expect(filterRect.right).toBeLessThanOrEqual(window.innerWidth + 1);
        expect(filterRect.left).toBeGreaterThanOrEqual(-1);
        for (const button of Array.from(document.querySelectorAll('button'))) {
          const rect = button.getBoundingClientRect();
          expect(rect.right, `button "${button.textContent?.slice(0, 40)}" clipped`).toBeLessThanOrEqual(
            window.innerWidth + 1,
          );
        }
      }
    } finally {
      unmount();
    }
  });

  it('keeps DataTable loading/empty states visible when the table is hidden on mobile', async () => {
    const { unmount } = render(
      <DataTable<Row>
        data={[]}
        ariaLabel="Agents inventory"
        responsive
        isLoading
        loadingMessage="Loading agents…"
        getRowKey={(row) => row.id}
        columns={columns}
      />,
    );
    try {
      await page.viewport(320, 568);
      await new Promise((resolve) => setTimeout(resolve, 50));
      const table = document.querySelector('.data-table') as HTMLElement;
      const cards = document.querySelector('.data-table-cards') as HTMLElement;
      expect(table).toBeTruthy();
      expect(cards).toBeTruthy();
      // At 320px the table is display:none and cards take over (mutually exclusive).
      expect(getComputedStyle(table).display).toBe('none');
      expect(getComputedStyle(cards).display).not.toBe('none');
      expect(cards.textContent).toContain('Loading agents…');
    } finally {
      unmount();
    }
  });

  it('stacks provider cards and tier fields at 320px without squeezed label columns', async () => {
    const host = renderProviderComposition();
    try {
      for (const viewport of [
        { width: 320, height: 568 },
        { width: 390, height: 844 },
      ]) {
        await assertNoPageOverflow(viewport, 'provider');
        const actionsCell = host.querySelector('td[data-label="Actions"]') as HTMLElement;
        const actionsRect = actionsCell.getBoundingClientRect();
        expect(actionsRect.right).toBeLessThanOrEqual(window.innerWidth + 1);
        // Full record width: the actions cell spans nearly the whole card.
        const card = host.querySelector('tbody tr') as HTMLElement;
        const cardRect = card.getBoundingClientRect();
        expect(actionsRect.width).toBeGreaterThanOrEqual(cardRect.width * 0.8);
        for (const input of Array.from(host.querySelectorAll('input, select'))) {
          const rect = (input as HTMLElement).getBoundingClientRect();
          expect(rect.right, `${input.tagName} spills right`).toBeLessThanOrEqual(window.innerWidth + 1);
        }
      }
    } finally {
      host.remove();
    }
  });
});
