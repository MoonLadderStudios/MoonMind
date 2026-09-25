import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page } from 'vitest/browser';
import { QueryClient } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';

import OmnigentInventoryPage from '../entrypoints/omnigent-inventory';
import { DataTable, type Column } from '../components/tables/DataTable';
import {
  ProviderProfilesManager,
  type ProviderProfile,
} from '../components/settings/ProviderProfilesManager';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser guardrail for MoonLadderStudios/MoonMind#4559. These cases use
// the production components and the production stylesheet inside the shared
// shell composition, asserting element bounds against the real viewport (the
// shell clips overflow, so a scrollbar-only assertion would miss the defect).

const MOBILE_320 = { width: 320, height: 568 } as const;
const MOBILE_390 = { width: 390, height: 844 } as const;
const DESKTOP = { width: 1280, height: 800 } as const;
const TOLERANCE = 1;

function shell(inner: string): string {
  return `<div class="dashboard-root"><div class="dashboard-content">${inner}</div></div>`;
}

function rightEdgeFitsViewport(element: Element): boolean {
  return element.getBoundingClientRect().right <= window.innerWidth + TOLERANCE;
}

function expectFitsViewport(element: Element, label: string): void {
  const rect = element.getBoundingClientRect();
  expect(
    rect.right,
    `${label} extends past the viewport (right=${rect.right.toFixed(1)}, viewport=${window.innerWidth})`,
  ).toBeLessThanOrEqual(window.innerWidth + TOLERANCE);
}

interface OverflowRow {
  id: string;
  name: string;
  model: string;
}

const overflowColumns: Column<OverflowRow>[] = [
  { key: 'name', header: 'Name' },
  { key: 'model', header: 'Model' },
];

const overflowRows: OverflowRow[] = [
  {
    id: 'row-1',
    name: 'synthetic-agent-with-a-very-long-unbroken-identifier-that-must-wrap',
    model: 'opencode-go/synthetic-model-with-an-extremely-long-unbroken-suffix-for-overflow',
  },
];

const syntheticProfile: ProviderProfile = {
  profile_id: 'synthetic-profile-with-a-very-long-unbroken-identifier-for-mobile',
  runtime_id: 'codex_cli',
  provider_id: 'openai',
  provider_label: 'Synthetic provider with a long label that must wrap on mobile',
  credential_source: 'oauth_volume',
  runtime_materialization_mode: 'oauth_home',
  secret_refs: {},
  volume_ref: 'synthetic-oauth-volume-with-a-long-unbroken-name',
  volume_mount_path: '/mnt/synthetic-oauth-volume',
  max_parallel_runs: 1,
  cooldown_after_429_seconds: 300,
  rate_limit_policy: 'backoff',
  enabled: true,
  is_default: true,
  model_tiers: [
    {
      label: 'Plan and verify with a deliberately long tier label for wrapping',
      model: 'synthetic-model-with-an-extremely-long-unbroken-identifier-for-tier-overflow',
      effort: 'xhigh',
    },
    { label: 'Implementation', model: 'gpt-5.5', effort: 'medium' },
  ],
  default_model_tier: 1,
};

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;

function mockFetchForInventory(): void {
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/omnigent/agent-profiles') {
      return { ok: true, json: async () => [] } as Response;
    }
    return {
      ok: true,
      json: async () => [
        {
          id: 'agent-with-a-very-long-unbroken-identifier-that-must-not-force-panning',
          name: 'Synthetic agent with a long description that must wrap inside the record',
          status: 'ready',
          description:
            'A synthetic description with enough words to wrap across several lines on a narrow phone viewport.',
        },
      ],
    } as Response;
  });
}

function mockFetchRejectAll(): void {
  fetchSpy = vi
    .spyOn(window, 'fetch')
    .mockRejectedValue(new Error('synthetic offline fixture'));
}

beforeEach(() => {
  window.localStorage.clear();
  document.body.innerHTML = '';
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy?.mockRestore();
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

describe('mobile overflow (MoonLadderStudios/MoonMind#4559)', () => {
  it('keeps responsive DataTable cards and their state fallbacks inside a 320px viewport', async () => {
    await page.viewport(MOBILE_320.width, MOBILE_320.height);
    document.body.innerHTML = shell('<div id="datatable-host"></div>');
    const host = document.getElementById('datatable-host')!;
    const { unmount: unmountTable } = renderWithClient(
      <DataTable
        data={overflowRows}
        columns={overflowColumns}
        getRowKey={(row) => row.id}
        responsive
        ariaLabel="Synthetic overflow table"
        rowActions={() => (
          <button type="button">Activate synthetic configuration</button>
        )}
      />,
      { container: host },
    );
    cleanupRender = unmountTable;

    const card = await waitFor(() => {
      const found = host.querySelector('.data-table-card');
      expect(found).not.toBeNull();
      return found!;
    });
    // The shared shell clips horizontal overflow, so assert the laid-out
    // bounds instead of the document scroll width.
    expectFitsViewport(card, 'responsive data-table card');
    const value = card.querySelector('.data-table-card__value')!;
    // Values use the full record width rather than competing with a label column.
    const cardRect = card.getBoundingClientRect();
    const valueRect = value.getBoundingClientRect();
    const cardStyle = window.getComputedStyle(card);
    const cardPaddingLeft =
      Number.parseFloat(cardStyle.paddingLeft || '0') || 0;
    expect(
      valueRect.left - cardRect.left,
      'card value should start near the card edge, not beside a wide label column',
    ).toBeLessThanOrEqual(cardPaddingLeft + 8);
    expectFitsViewport(value, 'card value with long unbroken identifier');
  });

  it('renders DataTable loading, error, and empty states in the mobile card fallback', async () => {
    await page.viewport(MOBILE_320.width, MOBILE_320.height);
    document.body.innerHTML = shell('<div id="datatable-state-host"></div>');
    const host = document.getElementById('datatable-state-host')!;
    const { unmount } = renderWithClient(
      <DataTable
        data={[]}
        columns={overflowColumns}
        getRowKey={(row) => row.id}
        responsive
        isLoading
        loadingMessage="Loading synthetic rows…"
      />,
      { container: host },
    );
    cleanupRender = unmount;

    const cards = await waitFor(() => {
      const found = host.querySelector('.data-table-cards');
      expect(found).not.toBeNull();
      return found!;
    });
    expect(cards.textContent).toContain('Loading synthetic rows…');
    expectFitsViewport(cards, 'loading state card');
  });

  it('keeps the agents inventory header, filter, and records inside a 320px viewport', async () => {
    await page.viewport(MOBILE_320.width, MOBILE_320.height);
    window.history.replaceState({}, '', '/omnigent/agents');
    mockFetchForInventory();
    const { unmount } = renderWithClient(
      <BrowserRouter>
        <OmnigentInventoryPage
          payload={{
            page: 'omnigent-inventory',
            apiBase: '/api',
            features: { omnigentAgents: true },
            initialData: { uiEndpoints: { omnigentAgents: '/api/omnigent/api/agents' } },
          }}
        />
      </BrowserRouter>,
      {
        container: (() => {
          document.body.innerHTML = shell('<div id="inventory-host"></div>');
          return document.getElementById('inventory-host')!;
        })(),
      },
    );
    cleanupRender = unmount;

    const search = await screen.findByRole('searchbox');
    expectFitsViewport(search, 'inventory filter field');
    // Ordinary inventory reads as records on mobile: no page-level panning.
    const cards = await waitFor(() => {
      const found = document.querySelectorAll('.data-table-card');
      expect(found.length).toBeGreaterThan(0);
      return found;
    });
    cards.forEach((card, index) => expectFitsViewport(card, `inventory record ${index}`));
    expect(
      document.documentElement.scrollWidth,
      'inventory page must not require sideways panning',
    ).toBeLessThanOrEqual(window.innerWidth + TOLERANCE);

    await page.viewport(MOBILE_390.width, MOBILE_390.height);
    cards.forEach((card, index) => expectFitsViewport(card, `inventory record ${index} at 390px`));
  });

  it('keeps provider records, actions, and the edit form inside a 320px viewport', async () => {
    await page.viewport(MOBILE_320.width, MOBILE_320.height);
    mockFetchRejectAll();
    document.body.innerHTML = shell('<div id="provider-host"></div>');
    const host = document.getElementById('provider-host')!;
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { unmount } = renderWithClient(
      <ProviderProfilesManager
        profiles={[syntheticProfile]}
        secretSlugs={['SYNTHETIC_API_KEY']}
        onNotice={() => {}}
        queryClient={queryClient}
        defaultTaskModelByRuntime={{}}
      />,
      { container: host },
    );
    cleanupRender = unmount;

    const record = await waitFor(() => {
      const found = host.querySelector('.provider-profiles-table tbody tr');
      expect(found).not.toBeNull();
      return found!;
    });
    expectFitsViewport(record, 'provider record');
    const recordRect = record.getBoundingClientRect();
    const actionsCell = host.querySelector(
      '.provider-profiles-table td[data-label="Actions"]',
    )!;
    const actionsRect = actionsCell.getBoundingClientRect();
    // Actions own the full record width instead of squeezing beside a label column.
    expect(
      actionsRect.left - recordRect.left,
      'actions should start near the record edge on mobile',
    ).toBeLessThanOrEqual(20);
    expectFitsViewport(actionsCell, 'provider actions cell');
    host.querySelectorAll('button').forEach((button) => {
      if (button.getBoundingClientRect().width > 0) {
        expectFitsViewport(button, `action "${button.textContent?.slice(0, 24)}"`);
      }
    });

    // Edit form: every fieldset and control stays inside its section.
    const editButton = within(record as HTMLElement).getByRole('button', { name: 'Edit' });
    editButton.click();
    const identityInput = await screen.findByDisplayValue(syntheticProfile.profile_id);
    expectFitsViewport(identityInput, 'identity profile id field');
    host.querySelectorAll('fieldset').forEach((fieldset, index) => {
      expectFitsViewport(fieldset, `provider form fieldset ${index}`);
    });
    host.querySelectorAll('input, select, textarea').forEach((control) => {
      const rect = control.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        expect(rightEdgeFitsViewport(control)).toBe(true);
      }
    });
    // Tier rows: heading names the tier, actions wrap in their own area.
    const tierItems = host.querySelectorAll('[data-tier-client-id]');
    expect(tierItems.length).toBeGreaterThan(0);
    tierItems.forEach((tier, index) => expectFitsViewport(tier, `tier row ${index}`));
  }, 30000);
});
