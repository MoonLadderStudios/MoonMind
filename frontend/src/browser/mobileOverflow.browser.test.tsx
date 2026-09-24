import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { render, screen, within } from '@testing-library/react';

import { DataTable, type Column } from '../components/tables/DataTable';
import '../styles/dashboard.css';

// Real-browser guardrail for MoonLadderStudios/MoonMind#4559. The four
// reported phone patterns (Agents inventory, Provider create form, saved
// Provider profiles, model/effort tiers) must fit a 320px viewport without
// sideways page panning, clipped fields, or squeezed label/value columns.
// `.dashboard-root` already clips horizontal overflow, so a document-width
// assertion alone cannot catch these defects: every assertion below compares
// representative element bounds against their real content/scroll ancestors.
//
// Run with `npm run ui:test:browser` (Chromium/Firefox; WebKit targeted leg
// via `MOONMIND_BROWSER_ENGINES=webkit`).

const VIEWPORTS = [320, 360, 390, 430, 768, 1024, 1440] as const;
const TOLERANCE_PX = 1.5;

const LONG_ID = 'a-very-long-unbroken-profile-identity-that-must-not-force-sideways-panning-0123456789';
const LONG_TEXT =
  'A long human-readable summary that must wrap naturally inside the available content width. '.repeat(4);

let host: HTMLElement;

function fitWithin(child: Element, ancestor: Element, label: string): void {
  const childRect = child.getBoundingClientRect();
  const ancestorRect = ancestor.getBoundingClientRect();
  expect(
    childRect.right,
    `${label}: right edge ${childRect.right.toFixed(1)} exceeds ancestor ${ancestorRect.right.toFixed(1)}`,
  ).toBeLessThanOrEqual(ancestorRect.right + TOLERANCE_PX);
  expect(
    childRect.left,
    `${label}: left edge ${childRect.left.toFixed(1)} escapes ancestor ${ancestorRect.left.toFixed(1)}`,
  ).toBeGreaterThanOrEqual(ancestorRect.left - TOLERANCE_PX);
}

function inventoryMarkup(): string {
  return `
    <div class="omnigent-inventory" aria-label="Agents harness">
      <header><p class="eyebrow">Omnigent</p><h1>Agents</h1><p>Available agent identities and runtime status.</p></header>
      <section aria-label="Agents inventory">
        <div class="omnigent-inventory__toolbar"><h2>Agents inventory</h2><button type="button">Refresh</button></div>
        <label><span>Filter agents</span><input type="search" value="" /></label>
        <div class="omnigent-inventory__table-wrap"><table>
          <thead><tr><th>Identity</th><th>Status</th><th>Summary</th><th>Freshness</th></tr></thead>
          <tbody><tr>
            <td data-label="Identity"><strong>Team codex</strong><small>${LONG_ID}@3</small></td>
            <td data-label="Status">active</td>
            <td data-label="Summary">${LONG_TEXT}</td>
            <td data-label="Freshness">just now</td>
          </tr></tbody>
        </table></div>
      </section>
    </div>`;
}

function providerTableMarkup(): string {
  return `
    <div class="provider-profiles-table-wrap mt-6 overflow-x-auto"><table class="provider-profiles-table min-w-full text-left text-sm">
      <thead><tr><th>Profile</th><th>Runtime</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody><tr>
        <td data-label="Profile"><div>${LONG_ID}</div></td>
        <td data-label="Runtime">codex_cli</td>
        <td data-label="Status"><span>Enabled</span><div>Readiness: ready</div></td>
        <td data-label="Actions"><div class="provider-profile-actions flex flex-wrap gap-2">
          <button type="button" class="rounded-full border px-3 py-1.5 text-xs">OAuth</button>
          <button type="button" class="rounded-full border px-3 py-1.5 text-xs">Validate OAuth ${LONG_ID}</button>
          <button type="button" class="rounded-full border px-3 py-1.5 text-xs">Make default</button>
          <button type="button" class="rounded-full border px-3 py-1.5 text-xs">Delete saved profile</button>
        </div></td>
      </tr></tbody>
    </table></div>`;
}

function providerFormMarkup(): string {
  return `
    <form class="provider-profile-form space-y-6" aria-label="Create Profile harness">
      <fieldset class="provider-profile-fieldset rounded-2xl border p-5 space-y-4">
        <legend class="px-2 text-sm font-semibold">Identity &mdash; required</legend>
        <div class="provider-profile-identity-grid grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <label class="flex flex-col gap-1.5 text-sm">Profile ID<input class="w-full rounded-xl border px-3 py-2 text-sm" value="${LONG_ID}" /></label>
          <label class="flex flex-col gap-1.5 text-sm">Runtime ID<input class="w-full rounded-xl border px-3 py-2 text-sm" value="codex_cli" /></label>
          <label class="flex flex-col gap-1.5 text-sm">Provider ID<input class="w-full rounded-xl border px-3 py-2 text-sm" value="openai" /></label>
          <label class="flex flex-col gap-1.5 text-sm">Account label<input class="w-full rounded-xl border px-3 py-2 text-sm" value="Team account" /></label>
        </div>
      </fieldset>
      <fieldset class="provider-profile-fieldset rounded-2xl border p-5 space-y-4" aria-label="Model and effort tiers">
        <legend class="px-2 text-sm font-semibold">Model &amp; effort tiers</legend>
        <ol class="provider-tier-list space-y-4" aria-label="Model and effort tiers">
          <li class="provider-tier-card rounded-2xl border p-4 shadow-sm" data-tier-client-id="tier-1">
            <fieldset class="provider-tier-fieldset space-y-3">
              <legend class="provider-tier-legend"><span class="text-sm font-semibold">Tier 1<span>Default</span></span></legend>
              <div class="tier-card__actions">
                <button type="button" class="text-xs">Duplicate tier</button>
                <button type="button" class="text-xs">Remove tier</button>
              </div>
              <label class="flex flex-col gap-1.5 text-sm">Tier 1 model<select class="w-full rounded-xl border px-3 py-2 text-sm"><option>${LONG_ID}</option></select></label>
              <label class="flex flex-col gap-1.5 text-sm">Tier 1 effort<select class="w-full rounded-xl border px-3 py-2 text-sm"><option>high</option></select></label>
            </fieldset>
          </li>
        </ol>
      </fieldset>
    </form>`;
}

function mountHarness(): void {
  host.innerHTML = `${inventoryMarkup()}
    <section class="provider-profiles rounded-3xl border p-6">${providerTableMarkup()}${providerFormMarkup()}</section>`;
}

beforeEach(() => {
  document.body.style.margin = '0';
  host = document.createElement('main');
  host.style.minWidth = '0';
  host.style.width = '100%';
  document.body.appendChild(host);
  mountHarness();
});

afterEach(async () => {
  host.remove();
  document.body.style.margin = '';
  await page.viewport(1280, 800);
});

describe('mobile overflow repair (MoonMind#4559)', () => {
  it.each(VIEWPORTS)('keeps the Agents inventory within the content width at %spx', async (width) => {
    await page.viewport(width, 800);
    const inventory = host.querySelector<HTMLElement>('.omnigent-inventory')!;
    const wrap = host.querySelector<HTMLElement>('.omnigent-inventory__table-wrap')!;
    const filter = host.querySelector<HTMLInputElement>('.omnigent-inventory input[type="search"]')!;

    fitWithin(inventory, host, 'inventory root');
    fitWithin(filter, inventory, 'inventory filter field');
    // Ordinary inventory rows must not require panning the table region.
    expect(wrap.scrollWidth, `inventory table wrap scrolls sideways at ${width}px`).toBeLessThanOrEqual(
      wrap.clientWidth + TOLERANCE_PX,
    );
    for (const cell of Array.from(wrap.querySelectorAll('td'))) {
      fitWithin(cell, inventory, `inventory cell ${cell.getAttribute('data-label')}`);
    }
  });

  it.each(VIEWPORTS)('gives saved Provider records full-width details and actions at %spx', async (width) => {
    await page.viewport(width, 800);
    const row = host.querySelector<HTMLElement>('.provider-profiles-table tbody tr')!;
    const actionsCell = host.querySelector<HTMLElement>('.provider-profiles-table td[data-label="Actions"]')!;
    const actions = host.querySelector<HTMLElement>('.provider-profile-actions')!;

    fitWithin(row, host, 'provider record');
    if (width <= 720) {
      // Details and actions use the full record width instead of competing
      // with a wide label column.
      const rowRect = row.getBoundingClientRect();
      const actionsRect = actionsCell.getBoundingClientRect();
      expect(actionsRect.width, `actions squeeze beside the label column at ${width}px`).toBeGreaterThanOrEqual(
        rowRect.width - 48,
      );
    }
    fitWithin(actions, host, 'provider record actions');
    // Long action labels wrap at word boundaries in a comfortably wide
    // button instead of compressing into tall pills.
    for (const button of Array.from(actions.querySelectorAll('button'))) {
      expect(button.getBoundingClientRect().height, `tall pill button "${button.textContent?.slice(0, 24)}"`).toBeLessThanOrEqual(64);
    }
  });

  it.each(VIEWPORTS)('keeps Provider form sections and tier rows inside their sections at %spx', async (width) => {
    await page.viewport(width, 800);
    const form = host.querySelector<HTMLElement>('.provider-profile-form')!;
    fitWithin(form, host, 'provider form');
    for (const fieldset of Array.from(form.querySelectorAll('fieldset'))) {
      fitWithin(fieldset, form, `fieldset ${fieldset.getAttribute('aria-label') ?? fieldset.querySelector('legend')?.textContent?.trim()}`);
    }
    for (const control of Array.from(form.querySelectorAll('input, select, button'))) {
      fitWithin(control, form, `control ${(control as HTMLElement).getAttribute('aria-label') ?? control.textContent?.trim().slice(0, 32)}`);
    }
    // Tier identity stays grouped while Duplicate/Remove live in their own
    // wrapping action area, not inside the legend toolbar.
    const legendButtons = host.querySelectorAll('.provider-tier-fieldset > legend button');
    expect(legendButtons.length, 'tier actions must not compete inside the legend').toBe(0);
    const tierActions = host.querySelector<HTMLElement>('.tier-card__actions')!;
    fitWithin(tierActions, form, 'tier action area');
  });

  it('keeps a narrow container usable inside a wide viewport', async () => {
    await page.viewport(1280, 800);
    host.style.width = '300px';
    const form = host.querySelector<HTMLElement>('.provider-profile-form')!;
    const wrap = host.querySelector<HTMLElement>('.omnigent-inventory__table-wrap')!;
    fitWithin(form, host, 'narrow-container form');
    expect(wrap.scrollWidth, 'narrow-container inventory wrap scrolls sideways').toBeLessThanOrEqual(
      wrap.clientWidth + TOLERANCE_PX,
    );
  });
});

interface RecordRow {
  id: string;
  name: string;
}

const recordColumns: Column<RecordRow>[] = [
  { key: 'name', header: 'Name' },
  { key: 'id', header: 'Identity' },
];

const recordRows: RecordRow[] = [{ id: LONG_ID, name: 'Team codex' }];

describe('DataTable responsive states (MoonMind#4559)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('exposes loading, empty, and error states in the mobile card fallback', async () => {
    await page.viewport(320, 800);
    const states: Array<{ props: Partial<React.ComponentProps<typeof DataTable<RecordRow>>>; text: string }> = [
      { props: { isLoading: true, loadingMessage: 'Loading records…' }, text: 'Loading records…' },
      { props: { data: [], emptyMessage: 'No records yet.' }, text: 'No records yet.' },
      { props: { isError: true, errorMessage: 'Records failed to load.' }, text: 'Records failed to load.' },
    ];
    for (const state of states) {
      const view = render(
        <DataTable
          data={recordRows}
          columns={recordColumns}
          getRowKey={(row) => row.id}
          ariaLabel="Records"
          responsive
          {...state.props}
        />,
      );
      const cards = document.querySelector<HTMLElement>('.data-table-cards')!;
      expect(getComputedStyle(cards).display, `${state.text}: cards hidden at 320px`).not.toBe('none');
      expect(within(cards as HTMLElement).getByText(state.text), `${state.text}: missing from mobile cards`).toBeTruthy();
      const table = document.querySelector<HTMLElement>('.data-table')!;
      expect(getComputedStyle(table).display, `${state.text}: table still shown at 320px`).toBe('none');
      view.unmount();
      document.body.innerHTML = '';
    }
  });

  it('shows rows as full-width stacked cards without sideways scrolling at 320px', async () => {
    await page.viewport(320, 800);
    render(
      <DataTable
        data={recordRows}
        columns={recordColumns}
        getRowKey={(row) => row.id}
        ariaLabel="Records"
        responsive
        rowActions={() => <button type="button">Inspect Team codex record</button>}
      />,
    );
    expect(screen.getAllByRole('button', { name: 'Inspect Team codex record' }).length).toBeGreaterThanOrEqual(1);
    const card = document.querySelector<HTMLElement>('.data-table-card')!;
    const cards = document.querySelector<HTMLElement>('.data-table-cards')!;
    expect(card.getBoundingClientRect().width).toBeLessThanOrEqual(cards.getBoundingClientRect().width + TOLERANCE_PX);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
  });
});
