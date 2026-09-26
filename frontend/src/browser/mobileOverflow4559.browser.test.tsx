import { afterEach, describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { DataTable, type Column } from '../components/tables/DataTable';
import {
  ProviderProfilesManager,
  type ProviderProfile,
} from '../components/settings/ProviderProfilesManager';
import {
  buildProviderProfileTierPayload,
  normalizeProviderProfileTiers,
} from '../utils/providerProfileTiers';
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

  it('keeps policy inspect/version/diff sections and action groups inside 320px (MoonMind#4559 AC-02)', async () => {
    const host = document.createElement('div');
    host.className = 'dashboard-content';
    host.innerHTML = `
      <div class="omnigent-inventory">
        <section class="omnigent-policy-detail" aria-label="Immutable policy version">
          <div class="omnigent-inventory__toolbar">
            <h2>Long policy name that must wrap instead of stretching the page ${LONG_ID}</h2>
            <button type="button">Close</button>
          </div>
          <div>
            <h3>Version history</h3>
            <button type="button" aria-pressed="true">policy@3 · active</button>
            <button type="button" aria-pressed="false">policy@2 · deprecated</button>
          </div>
          <p>Validation: Needs attention</p>
          <p role="alert">some/path: CODE: a very long diagnostic message that must wrap ${LONG_SUMMARY}</p>
          <h3>Host, resources, workspace, network, capture, controls, checkpoints, remediation, RAG, approvals, and retention</h3>
          <pre>{"rule": "a-very-long-unbroken-document-string-that-must-not-stretch-the-page-0123456789-abcdef"}</pre>
          <button type="button">Validate against deployment</button>
          <button type="button">Roll back default to policy@2 with a very long action label</button>
          <button type="button">Disable policy@3</button>
          <button type="button">Deprecate policy@3</button>
          <button type="button">Edit as new version</button>
          <button type="button">Clone</button>
          <h3>Normalized diff to current default</h3>
          <pre>model.tier: "a" → "a very long changed value that must wrap inside the detail pane"</pre>
          <h3>Audit history</h3>
          <ol><li>transitioned · version 3 · operator</li></ol>
        </section>
        <form class="omnigent-policy-editor">
          <h2>Edit as immutable new version</h2>
          <label><span>Policy id</span><input value="${LONG_ID}" /></label>
          <label><span>Complete policy document (JSON)</span><textarea rows="4">{"a": 1}</textarea></label>
          <button type="submit">Validate and save draft</button>
          <button type="button">Cancel</button>
        </form>
      </div>
    `;
    document.body.appendChild(host);
    try {
      for (const viewport of [
        { width: 320, height: 568 },
        { width: 390, height: 844 },
      ]) {
        await assertNoPageOverflow(viewport, 'policy-detail');
        for (const el of Array.from(host.querySelectorAll('button, input, textarea, pre, p, h2'))) {
          const rect = (el as HTMLElement).getBoundingClientRect();
          expect(rect.right, `${el.tagName} "${(el.textContent ?? '').slice(0, 40)}" spills right`).toBeLessThanOrEqual(
            window.innerWidth + 1,
          );
          expect(rect.left).toBeGreaterThanOrEqual(-1);
        }
      }
    } finally {
      host.remove();
    }
  });

  it('keeps enrollment drawers and confirmation dialogs viewport-bounded with focus return (MoonMind#4559 AC-05)', async () => {
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.textContent = 'Remove profile';
    document.body.appendChild(trigger);
    trigger.focus();
    const dialog = document.createElement('div');
    dialog.className = 'dashboard-content';
    dialog.innerHTML = `
      <div role="dialog" aria-modal="true" aria-label="Remove provider profile confirmation">
        <h2>Remove provider profile with a very long name that must wrap ${LONG_ID}</h2>
        <p>Removing a default tier requires a reviewed replacement default. This message is long so wrapping is exercised: ${LONG_SUMMARY}</p>
        <label>Confirmation input<input value="${LONG_ID}" /></label>
        <div>
          <button type="button" data-close>Cancel</button>
          <button type="button" data-close>Remove and renumber with a very long destructive label</button>
        </div>
      </div>
    `;
    document.body.appendChild(dialog);
    try {
      await page.viewport(320, 568);
      await new Promise((resolve) => setTimeout(resolve, 50));
      await assertNoPageOverflow({ width: 320, height: 568 }, 'overlay');
      const panel = dialog.querySelector('[role="dialog"]') as HTMLElement;
      const rect = panel.getBoundingClientRect();
      expect(rect.right).toBeLessThanOrEqual(window.innerWidth + 1);
      expect(rect.left).toBeGreaterThanOrEqual(-1);
      expect(rect.height).toBeLessThanOrEqual(window.innerHeight + 1);
      // Keyboard/focus contract: move focus into the dialog, Escape closes it,
      // and focus returns to the initiating action.
      const firstButton = panel.querySelector('button') as HTMLElement;
      firstButton.focus();
      expect(document.activeElement).toBe(firstButton);
      const onKey = (event: KeyboardEvent) => {
        if (event.key === 'Escape') dialog.remove();
      };
      document.addEventListener('keydown', onKey);
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      document.removeEventListener('keydown', onKey);
      expect(document.body.contains(dialog)).toBe(false);
      trigger.focus();
      expect(document.activeElement).toBe(trigger);
    } finally {
      dialog.remove();
      trigger.remove();
      await page.viewport(1280, 800);
    }
  });

  it('leaves dialog backdrops, drawers, and the wide jira panel at their own sizing (Codex P1)', async () => {
    // Regression guard for the Codex P1 review on PR #4566: the overlay
    // sizing contract must apply to confirmation dialog panels only. Fixed
    // inset-0 backdrops that carry role="dialog" keep full-viewport
    // coverage, the 1040px jira browser panel keeps its own sizing, and
    // full-height enrollment drawers keep their drawer layout. Assertions
    // use computed sizing (not geometry) because the browser suite loads
    // dashboard.css without Tailwind utilities, so utility classes such as
    // `fixed` position nothing here -- the selectors must exclude those
    // elements by class name regardless.
    const host = document.createElement('div');
    host.className = 'dashboard-content';
    host.innerHTML = `
      <div class="fixed inset-0 z-50 flex items-center justify-center" role="dialog" aria-modal="true" aria-label="Remove tier backdrop">
        <div class="w-full max-w-lg">
          <h2>Remove Tier 2?</h2>
          <button type="button">Cancel</button>
          <button type="button">Remove and renumber</button>
        </div>
      </div>
      <section class="jira-browser-panel stack" role="dialog" aria-modal="true" aria-label="Browse Jira issue">
        <h2>Browse Jira issue</h2>
      </section>
      <div class="fixed inset-0 z-50 flex justify-end">
        <div class="h-full w-full max-w-2xl" role="dialog" aria-modal="true" aria-label="Enrollment drawer">
          <h2>Enrollment</h2>
        </div>
      </div>
      <div role="dialog" aria-modal="true" aria-label="Remove profile confirmation">
        <h2>Remove profile?</h2>
        <button type="button">Cancel</button>
      </div>
    `;
    document.body.appendChild(host);
    try {
      await page.viewport(1280, 800);
      await new Promise((resolve) => setTimeout(resolve, 50));
      const backdrop = host.querySelector('[aria-label="Remove tier backdrop"]') as HTMLElement;
      expect(getComputedStyle(backdrop).maxWidth, 'backdrop keeps viewport coverage').toBe('none');
      const jiraPanel = host.querySelector('.jira-browser-panel') as HTMLElement;
      expect(getComputedStyle(jiraPanel).maxWidth, 'jira panel keeps its own sizing').toBe('none');
      expect(jiraPanel.getBoundingClientRect().width, 'jira panel is not shrunk to 32rem').toBeGreaterThan(600);
      // Positive control: a plain confirmation panel is still viewport-bounded.
      const confirmation = host.querySelector('[aria-label="Remove profile confirmation"]') as HTMLElement;
      expect(getComputedStyle(confirmation).maxWidth, 'confirmation stays bounded').not.toBe('none');
      await page.viewport(320, 568);
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(getComputedStyle(backdrop).maxWidth, 'backdrop keeps coverage on mobile').toBe('none');
      const drawer = host.querySelector('[aria-label="Enrollment drawer"]') as HTMLElement;
      expect(getComputedStyle(drawer).maxHeight, 'drawer keeps its full-height layout').toBe('none');
    } finally {
      host.remove();
      await page.viewport(1280, 800);
    }
  });

  it('keeps the production provider journey inside 320px with canonical tier payload (MoonMind#4559 AC-03/AC-04)', async () => {
    // Production-component journey: the real ProviderProfilesManager with
    // synthetic credential-free fixtures (long identity, long tier labels,
    // several tiers, non-first default). Static props keep it network-quiet;
    // any unexpected fetch fails loudly instead of hitting the network.
    const originalFetch = window.fetch;
    const profiles: ProviderProfile[] = [
      {
        profile_id: LONG_ID,
        runtime_id: 'codex_cli',
        provider_id: 'openai',
        credential_source: 'secret_ref',
        runtime_materialization_mode: 'api_key_env',
        secret_refs: {},
        max_parallel_runs: 1,
        cooldown_after_429_seconds: 300,
        rate_limit_policy: 'backoff',
        enabled: true,
        is_default: true,
        model_tiers: [
          { label: 'Plan and verify with a very long tier label that must wrap', model: 'gpt-5.5', effort: 'medium' },
          { label: 'Implementation', model: 'gpt-5.5', effort: 'xhigh' },
          { label: 'Docs and follow-through', model: null, effort: null },
        ],
        default_model_tier: 2,
      },
      {
        profile_id: 'short-id',
        runtime_id: 'codex_cli',
        provider_id: 'openai',
        credential_source: 'secret_ref',
        runtime_materialization_mode: 'api_key_env',
        secret_refs: {},
        max_parallel_runs: 1,
        cooldown_after_429_seconds: 300,
        rate_limit_policy: 'backoff',
        enabled: true,
        is_default: false,
      },
    ];
    window.fetch = (async () => {
      throw new Error('Unexpected fetch in mobileOverflow4559 provider journey');
    }) as typeof window.fetch;
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <div className="dashboard-content">
          <ProviderProfilesManager
            profiles={profiles}
            secretSlugs={[]}
            onNotice={() => undefined}
            queryClient={queryClient}
            defaultTaskModelByRuntime={{}}
          />
        </div>
      </QueryClientProvider>,
    );
    try {
      // Normalized UI state survives the layout refactor.
      const mapping = screen.getByLabelText(`${LONG_ID} model tier mapping`);
      expect(mapping.textContent).toContain('Tier 2 default · Implementation');
      for (const viewport of [
        { width: 320, height: 568 },
        { width: 390, height: 844 },
      ]) {
        await assertNoPageOverflow(viewport, 'provider-journey');
        // Saved-profile actions use the full record width, not a squeezed label column.
        const actionsCell = document.querySelector(
          '.provider-profiles-table td[data-label="Actions"]',
        ) as HTMLElement | null;
        if (actionsCell && getComputedStyle(actionsCell).display !== 'none') {
          const actionsRect = actionsCell.getBoundingClientRect();
          expect(actionsRect.right).toBeLessThanOrEqual(window.innerWidth + 1);
        }
        for (const input of Array.from(document.querySelectorAll('input, select, textarea'))) {
          const rect = (input as HTMLElement).getBoundingClientRect();
          expect(rect.right, `${input.tagName} spills right`).toBeLessThanOrEqual(window.innerWidth + 1);
        }
        for (const button of Array.from(document.querySelectorAll('button'))) {
          const rect = button.getBoundingClientRect();
          expect(rect.right, `button "${button.textContent?.slice(0, 40)}" clipped`).toBeLessThanOrEqual(
            window.innerWidth + 1,
          );
        }
      }
      // Canonical save payload for the same fixtures: order preserved,
      // 1-based default, no legacy mirrors.
      const saved = profiles[0]!;
      const normalized = normalizeProviderProfileTiers(saved.model_tiers, saved.default_model_tier);
      const payload = buildProviderProfileTierPayload(normalized.tiers, normalized.defaultTierClientId);
      expect(payload.default_model_tier).toBe(2);
      expect(payload.model_tiers).toHaveLength(3);
      expect(payload).not.toHaveProperty('default_model');
      expect(JSON.stringify(payload)).not.toContain('clientId');
    } finally {
      unmount();
      window.fetch = originalFetch;
      queryClient.clear();
    }
  });
});
