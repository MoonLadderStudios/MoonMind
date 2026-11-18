import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import deliveryFixture from '../../../../tests/fixtures/provider/account_usage_settings_delivery.json';
import type { components } from '../../generated/openapi';
import { DashboardToastProvider } from '../dashboard/DashboardToast';
import { accountUsageProjection, usageWindow, type ProjectionOverrides } from './accountUsageTestFixtures';
import { profileAction } from './profileActionsTestHelpers';
import {
  fetchProviderProfiles,
  PROVIDER_PROFILE_CREATION_CHOICES_QUERY_KEY,
  PROVIDER_PROFILE_QUERY_KEY,
  ProviderProfilesManager,
  type ProviderProfile,
} from './ProviderProfilesManager';
import { ACCOUNT_USAGE_REFRESH_PATH } from './useProviderAccountUsageRefresh';

// Settings → Providers & Secrets account usage (MoonLadderStudios/MoonMind#5366):
// the list and editor render MoonMind's cached projection, and one
// visibility-aware coordinator requests collection without disturbing drafts.

type RefreshRequest = components['schemas']['ProviderAccountUsageRefreshRequest'];
type RefreshResult = components['schemas']['ProviderAccountUsageRefreshResult'];

const NOW = Date.parse('2026-11-18T20:00:00Z');
const TEN_MINUTES_AGO = '2026-11-18T19:50:00Z';

function oauthProfile(profileId: string, usage: ProjectionOverrides | null): ProviderProfile {
  return {
    profile_id: profileId,
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    credential_source: 'oauth_volume',
    runtime_materialization_mode: 'oauth_home',
    authentication_method: 'oauth',
    secret_refs: {},
    volume_ref: `${profileId}_volume`,
    volume_mount_path: '/home/app/.codex',
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: true,
    is_default: false,
    auth_state: 'connected',
    disabled_reason: null,
    account_label: 'Codex account',
    readiness: { status: 'ready', launch_ready: true, summary: 'Ready to launch.', checks: [] },
    account_usage: usage === null ? null : accountUsageProjection({ profile_id: profileId, ...usage }),
  };
}

const STALE: ProjectionOverrides = {
  status: 'stale',
  windows: [usageWindow({ observed_at: TEN_MINUTES_AGO, fresh_until: '2026-11-18T19:55:00Z', freshness: 'stale', last_known: true })],
  refresh: { last_success_at: TEN_MINUTES_AGO, last_attempt_at: TEN_MINUTES_AGO },
};

interface Harness {
  queryClient: QueryClient;
  refreshRequests: Array<{ body: RefreshRequest; signal: AbortSignal | null | undefined }>;
  listFetches: () => number;
  allRequests: Array<{ url: string; method: string; body: string }>;
  setProfiles: (profiles: ProviderProfile[]) => void;
  unmount: () => void;
}

let visibility: DocumentVisibilityState = 'visible';

function setVisibility(next: DocumentVisibilityState) {
  visibility = next;
  document.dispatchEvent(new Event('visibilitychange'));
}

function json(payload: unknown, ok = true): Response {
  return { ok, status: ok ? 200 : 500, json: async () => payload } as Response;
}

function renderLiveManager(
  initialProfiles: ProviderProfile[],
  options: {
    /** Answers each refresh request; default accepts every profile. */
    respond?: (body: RefreshRequest) => RefreshResult[] | Promise<RefreshResult[]>;
    /** Profiles each list re-read returns; default repeats the current list. */
    nextList?: () => ProviderProfile[];
    /** Fail this many list re-reads before answering. */
    failListReads?: number;
  } = {},
): Harness {
  let current = initialProfiles;
  let listFetches = 0;
  const refreshRequests: Harness['refreshRequests'] = [];
  const allRequests: Harness['allRequests'] = [];
  const accept = (body: RefreshRequest): RefreshResult[] =>
    body.profile_ids.map((profileId) => ({
      profile_id: profileId,
      credential_generation:
        current.find((p) => p.profile_id === profileId)?.account_usage?.credential_generation ?? 1,
      outcome: 'accepted',
      refresh_state: 'queued',
      request_id: `req-${profileId}`,
      coalesced: false,
    }));
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      allRequests.push({ url, method, body: typeof init?.body === 'string' ? init.body : '' });
      if (url === '/api/v1/provider-profiles' && method === 'GET') {
        listFetches += 1;
        if (options.failListReads && options.failListReads > 0) {
          options.failListReads -= 1;
          return json({ detail: 'unavailable' }, false);
        }
        if (options.nextList) current = options.nextList();
        return json(current);
      }
      if (url === ACCOUNT_USAGE_REFRESH_PATH && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as RefreshRequest;
        refreshRequests.push({ body, signal: init?.signal });
        return json({ results: await (options.respond ?? accept)(body) });
      }
      return json({}, false);
    }),
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(PROVIDER_PROFILE_CREATION_CHOICES_QUERY_KEY, {
    version: 'provider-profile-creation-v1',
    profile_id_max_length: 128,
    harnesses: [],
  });

  function LiveManager() {
    const { data = [] } = useQuery<ProviderProfile[]>({
      queryKey: PROVIDER_PROFILE_QUERY_KEY,
      queryFn: fetchProviderProfiles,
      initialData: initialProfiles,
      staleTime: Infinity,
    });
    return (
      <ProviderProfilesManager
        profiles={data}
        secretSlugs={[]}
        onNotice={() => undefined}
        queryClient={queryClient}
        canWriteProviderProfiles
      />
    );
  }

  const view = render(
    <QueryClientProvider client={queryClient}>
      <DashboardToastProvider>
        <LiveManager />
      </DashboardToastProvider>
    </QueryClientProvider>,
  );
  return {
    queryClient,
    refreshRequests,
    listFetches: () => listFetches,
    allRequests,
    setProfiles: (profiles) => {
      current = profiles;
      act(() => {
        queryClient.setQueryData(PROVIDER_PROFILE_QUERY_KEY, profiles);
      });
    },
    unmount: view.unmount,
  };
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function usageGroup(profileId: string): HTMLElement {
  return screen.getAllByRole('group', { name: `Account and usage for ${profileId}` })[0]!;
}

function text(element: Element): string {
  return (element.textContent ?? '').replace(/\s+/g, ' ');
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
  vi.setSystemTime(NOW);
  visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('Provider Profile account usage in Settings', () => {
  it('places the compact block inside the existing Provider cell without new columns', async () => {
    const harness = renderLiveManager([oauthProfile('codex-oauth', {})]);
    await advance(10);

    expect(screen.getAllByRole('columnheader')).toHaveLength(6);
    const providerCell = document.querySelector('#provider-profile-codex-oauth td[data-label="Provider"]')!;
    const group = within(providerCell as HTMLElement).getByRole('group', {
      name: 'Account and usage for codex-oauth',
    });
    expect(text(group)).toContain('personal@example.com');
    expect(within(group).getByText('36% remaining')).toBeTruthy();
    expect(within(group).getByText('Usage details')).toBeTruthy();
    // Observation status sits beside, not instead of, connection and readiness.
    const statusCell = document.querySelector('#provider-profile-codex-oauth td[data-label="Status"]')!;
    expect(text(statusCell)).toContain('Enabled');
    expect(text(statusCell)).toContain('Readiness: Ready');
    // A fresh observation needs no collection.
    expect(harness.refreshRequests).toHaveLength(0);
  });

  it('requests one batched visibility refresh for stale displayed profiles only', async () => {
    const harness = renderLiveManager([
      oauthProfile('stale-profile', STALE),
      oauthProfile('never-collected', {
        status: 'unknown',
        windows: [],
        account: { status: 'unknown', email: null, display_name: null, plan: null, observed_at: null },
        refresh: { last_success_at: null, last_attempt_at: null },
      }),
      oauthProfile('fresh-profile', {}),
      oauthProfile('unsupported-profile', { status: 'not_supported', windows: [] }),
      oauthProfile('in-flight-profile', { ...STALE, refresh: { ...STALE.refresh, state: 'running' } }),
      oauthProfile('backoff-profile', {
        ...STALE,
        refresh: { ...STALE.refresh, state: 'backoff', next_allowed_at: '2026-11-18T20:10:00Z' },
      }),
    ]);
    await advance(10);

    expect(harness.refreshRequests).toHaveLength(1);
    expect(harness.refreshRequests[0]!.body).toEqual({
      profile_ids: ['stale-profile', 'never-collected'],
      trigger: 'visibility',
    });
  });

  it('re-reads the cached list when the server already holds a newer collection', async () => {
    const newer = oauthProfile('codex-oauth', {
      snapshot_revision: 8,
      windows: [usageWindow({ used_percent: 70, remaining_percent: 30 })],
    });
    const harness = renderLiveManager([oauthProfile('codex-oauth', STALE)], {
      respond: () => [
        {
          profile_id: 'codex-oauth',
          credential_generation: 3,
          outcome: 'already_fresh',
          refresh_state: 'idle',
          coalesced: false,
          retry_after_seconds: 240,
        },
      ],
      nextList: () => [newer],
    });
    await advance(10);

    expect(harness.refreshRequests).toHaveLength(1);
    expect(harness.listFetches()).toBe(1);
    expect(within(usageGroup('codex-oauth')).getByText('30% remaining')).toBeTruthy();

    // The re-read itself requests no further collection.
    await advance(60_000);
    expect(harness.refreshRequests).toHaveLength(1);
    expect(harness.listFetches()).toBe(1);
  });

  it('asks again about a deferred profile and leaves the busy notice once the deferral ends', async () => {
    const deferred = oauthProfile('codex-oauth', {
      refresh: { state: 'deferred', request_id: 'req-busy' },
    });
    let deferralOver = false;
    const harness = renderLiveManager([deferred], {
      respond: () => [
        deferralOver
          ? {
              profile_id: 'codex-oauth',
              credential_generation: 3,
              outcome: 'already_fresh',
              refresh_state: 'idle',
              coalesced: false,
            }
          : {
              profile_id: 'codex-oauth',
              credential_generation: 3,
              outcome: 'deferred_busy',
              refresh_state: 'deferred',
              request_id: 'req-busy',
              coalesced: false,
              retry_after_seconds: 120,
            },
      ],
      nextList: () => [oauthProfile('codex-oauth', {})],
    });
    await advance(10);

    expect(harness.refreshRequests.map((request) => request.body)).toEqual([
      { profile_ids: ['codex-oauth'], trigger: 'visibility' },
    ]);
    // Still deferred: the cached list is current, so nothing is re-read.
    expect(harness.listFetches()).toBe(0);
    expect(text(usageGroup('codex-oauth'))).toContain('This profile is busy');

    deferralOver = true;
    await advance(5 * 60_000);
    expect(harness.refreshRequests).toHaveLength(2);
    expect(harness.listFetches()).toBe(1);
    expect(text(usageGroup('codex-oauth'))).not.toContain('This profile is busy');
  });

  it('pauses while hidden and resumes with a stale check when visible again', async () => {
    visibility = 'hidden';
    const harness = renderLiveManager([oauthProfile('codex-oauth', STALE)]);
    await advance(10 * 60_000);
    expect(harness.refreshRequests).toHaveLength(0);

    setVisibility('visible');
    await advance(10);
    expect(harness.refreshRequests).toHaveLength(1);

    // About every five minutes while visible, one batch for what is still stale.
    await advance(5 * 60_000);
    expect(harness.refreshRequests).toHaveLength(2);

    setVisibility('hidden');
    const listReads = harness.listFetches();
    await advance(20 * 60_000);
    expect(harness.refreshRequests).toHaveLength(2);
    expect(harness.listFetches()).toBe(listReads);
  });

  it('keeps one coordinator for the list and editor and never storms the refresh API', async () => {
    const harness = renderLiveManager([oauthProfile('codex-oauth', STALE)], {
      respond: () => new Promise(() => undefined),
    });
    fireEvent.click(profileAction('Edit', 'codex-oauth'));
    expect(screen.getAllByRole('group', { name: 'Account and usage for codex-oauth' })).toHaveLength(2);

    for (let i = 0; i < 5; i += 1) {
      setVisibility('visible');
      window.dispatchEvent(new Event('focus'));
      await advance(1_000);
    }
    expect(harness.refreshRequests).toHaveLength(1);
    expect(harness.refreshRequests[0]!.body.profile_ids).toEqual(['codex-oauth']);
  });

  it('shows an accepted refresh as checking and re-reads the cache until the observation lands', async () => {
    let reads = 0;
    const queued = oauthProfile('codex-oauth', { ...STALE, refresh: { ...STALE.refresh, state: 'queued' } });
    const landed = oauthProfile('codex-oauth', {
      snapshot_revision: 8,
      windows: [usageWindow({ used_percent: 70, remaining_percent: 30 })],
    });
    const harness = renderLiveManager([oauthProfile('codex-oauth', STALE)], {
      nextList: () => {
        reads += 1;
        return reads < 2 ? [queued] : [landed];
      },
    });
    await advance(10);

    const group = usageGroup('codex-oauth');
    expect(within(group).getByRole('status').textContent).toBe('Checking for updated usage…');
    // Accepted is not completed: the cached reading and its real age stay.
    expect(within(group).getByText('36% remaining')).toBeTruthy();
    expect(text(group)).toContain('Updated 10 minutes ago');

    await advance(2_000);
    expect(harness.listFetches()).toBe(1);
    expect(within(usageGroup('codex-oauth')).getByRole('status')).toBeTruthy();

    await advance(4_000);
    expect(harness.listFetches()).toBe(2);
    expect(within(usageGroup('codex-oauth')).getByText('30% remaining')).toBeTruthy();
    expect(within(usageGroup('codex-oauth')).queryByRole('status')).toBeNull();

    await advance(4 * 60_000);
    expect(harness.listFetches()).toBe(2);
  });

  it('keeps waiting through a failed cache re-read instead of settling on old data', async () => {
    const landed = oauthProfile('codex-oauth', {
      snapshot_revision: 8,
      windows: [usageWindow({ used_percent: 70, remaining_percent: 30 })],
    });
    const harness = renderLiveManager([oauthProfile('codex-oauth', STALE)], {
      failListReads: 1,
      nextList: () => [landed],
    });
    await advance(10);

    await advance(2_000);
    expect(harness.listFetches()).toBe(1);
    expect(within(usageGroup('codex-oauth')).getByRole('status').textContent).toBe('Checking for updated usage…');

    await advance(4_000);
    expect(harness.listFetches()).toBe(2);
    expect(within(usageGroup('codex-oauth')).getByText('30% remaining')).toBeTruthy();
    expect(within(usageGroup('codex-oauth')).queryByRole('status')).toBeNull();
  });

  it('bounds cache re-reads for an attempt that never finishes and stops when hidden', async () => {
    const queued = oauthProfile('codex-oauth', { ...STALE, refresh: { ...STALE.refresh, state: 'queued' } });
    const harness = renderLiveManager([oauthProfile('codex-oauth', STALE)], { nextList: () => [queued] });
    await advance(10);

    await advance(4 * 60_000);
    const bounded = harness.listFetches();
    expect(bounded).toBeGreaterThan(0);
    expect(bounded).toBeLessThanOrEqual(6);

    setVisibility('hidden');
    await advance(4 * 60_000);
    expect(harness.listFetches()).toBe(bounded);
  });

  it('stops re-reading the cache as soon as the page is hidden', async () => {
    const queued = oauthProfile('codex-oauth', { ...STALE, refresh: { ...STALE.refresh, state: 'queued' } });
    const harness = renderLiveManager([oauthProfile('codex-oauth', STALE)], { nextList: () => [queued] });
    await advance(10);
    expect(harness.refreshRequests).toHaveLength(1);

    setVisibility('hidden');
    await advance(3 * 60_000);
    expect(harness.listFetches()).toBe(0);
  });

  it('honors the backend minimum interval and Retry-After for manual Refresh', async () => {
    const harness = renderLiveManager([oauthProfile('codex-oauth', {})], {
      respond: (body) => [
        {
          profile_id: body.profile_ids[0]!,
          credential_generation: 3,
          outcome: 'backoff',
          refresh_state: 'idle',
          coalesced: false,
          retry_after_seconds: 45,
          next_allowed_at: '2026-11-18T20:00:45Z',
        },
      ],
    });
    await advance(10);

    const refresh = within(usageGroup('codex-oauth')).getByRole('button', {
      name: 'Refresh account usage for codex-oauth',
    }) as HTMLButtonElement;
    fireEvent.click(refresh);
    await advance(10);

    expect(harness.refreshRequests.map((request) => request.body)).toEqual([
      { profile_ids: ['codex-oauth'], trigger: 'manual' },
    ]);
    expect(refresh.disabled).toBe(true);
    expect(text(usageGroup('codex-oauth'))).toContain('Refresh available in 1 min');

    fireEvent.click(refresh);
    await advance(10);
    expect(harness.refreshRequests).toHaveLength(1);

    await advance(45_000);
    expect(refresh.disabled).toBe(false);
  });

  it('refreshes from the editor without submitting it or disturbing the draft', async () => {
    let reads = 0;
    const landed = oauthProfile('codex-oauth', {
      snapshot_revision: 8,
      windows: [usageWindow({ used_percent: 70, remaining_percent: 30 })],
    });
    const harness = renderLiveManager([oauthProfile('codex-oauth', {})], {
      nextList: () => {
        reads += 1;
        return [landed];
      },
    });
    fireEvent.click(profileAction('Edit', 'codex-oauth'));

    const authLegend = screen.getByText('Authentication and readiness', { selector: 'legend' });
    const section = screen.getByRole('region', { name: 'Account & usage' });
    expect(authLegend.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(section.closest('form')).toBeTruthy();
    expect(text(section)).toContain('personal@example.com');

    const label = screen.getByLabelText(/Account label/) as HTMLInputElement;
    expect(label.value).toBe('Codex account');
    fireEvent.change(label, { target: { value: 'My personal plan' } });
    label.focus();

    fireEvent.click(within(section).getByRole('button', { name: 'Refresh account usage for codex-oauth' }));
    await advance(10);
    await advance(2_000);

    expect(reads).toBe(1);
    expect(within(screen.getByRole('region', { name: 'Account & usage' })).getByText('30% remaining')).toBeTruthy();
    expect((screen.getByLabelText(/Account label/) as HTMLInputElement).value).toBe('My personal plan');
    expect(document.activeElement).toBe(screen.getByLabelText(/Account label/));
    expect(screen.getByText('Edit Profile: codex-oauth')).toBeTruthy();
    // Only the refresh command and cache re-reads were sent; nothing was saved.
    expect(
      harness.allRequests.filter(
        (request) =>
          request.method !== 'GET' && request.url !== ACCOUNT_USAGE_REFRESH_PATH,
      ),
    ).toEqual([]);
  });

  it('discards a refresh answer that belongs to a replaced account', async () => {
    let answer!: (results: RefreshResult[]) => void;
    const harness = renderLiveManager([oauthProfile('codex-oauth', {})], {
      respond: () => new Promise((resolve) => (answer = resolve)),
    });
    await advance(10);
    fireEvent.click(
      within(usageGroup('codex-oauth')).getByRole('button', { name: 'Refresh account usage for codex-oauth' }),
    );
    await advance(10);
    expect(harness.refreshRequests).toHaveLength(1);

    harness.setProfiles([
      oauthProfile('codex-oauth', {
        credential_generation: 4,
        snapshot_revision: 1,
        account: { email: 'work@example.com', display_name: null },
      }),
    ]);
    await advance(10);
    expect(text(usageGroup('codex-oauth'))).toContain('work@example.com');
    expect(text(usageGroup('codex-oauth'))).not.toContain('personal@example.com');

    await act(async () => {
      answer([
        {
          profile_id: 'codex-oauth',
          credential_generation: 3,
          outcome: 'accepted',
          refresh_state: 'queued',
          request_id: 'req-old',
          coalesced: false,
        },
      ]);
    });
    await advance(10_000);

    expect(within(usageGroup('codex-oauth')).queryByRole('status')).toBeNull();
    expect(harness.listFetches()).toBe(0);
    expect(
      (within(usageGroup('codex-oauth')).getByRole('button', {
        name: 'Refresh account usage for codex-oauth',
      }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('cancels in-flight refresh requests when Settings unmounts', async () => {
    const harness = renderLiveManager([oauthProfile('codex-oauth', STALE)], {
      respond: () => new Promise(() => undefined),
    });
    await advance(10);
    const signal = harness.refreshRequests[0]!.signal;
    expect(signal?.aborted).toBe(false);

    harness.unmount();
    expect(signal?.aborted).toBe(true);
    await advance(10 * 60_000);
    expect(harness.refreshRequests).toHaveLength(1);
  });

  it('keeps account email and quota metadata out of URLs, history and browser storage', async () => {
    const harness = renderLiveManager([oauthProfile('codex-oauth', STALE)]);
    await advance(10);
    fireEvent.click(profileAction('Edit', 'codex-oauth'));
    await advance(10);

    const leaked = (value: string) => value.includes('personal@example.com') || value.includes('remaining');
    expect(harness.allRequests.some((request) => leaked(request.url))).toBe(false);
    expect(harness.refreshRequests.every((request) => !leaked(JSON.stringify(request.body)))).toBe(true);
    expect(leaked(window.location.href)).toBe(false);
    const stored = [window.localStorage, window.sessionStorage]
      .flatMap((storage) => Object.keys(storage).map((key) => `${key}=${storage.getItem(key)}`))
      .join('\n');
    expect(stored.includes('personal@example.com')).toBe(false);
  });

  it('uses unsupported-method copy for API-key profiles and does not request collection', async () => {
    const apiKey: ProviderProfile = {
      ...oauthProfile('codex-api-key', { status: 'not_supported', windows: [] }),
      authentication_method: 'api_key',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
    };
    const harness = renderLiveManager([apiKey]);
    await advance(10);

    expect(text(usageGroup('codex-api-key'))).toContain(
      'Subscription weekly usage not available for this authentication method',
    );
    expect(harness.refreshRequests).toHaveLength(0);
  });
});

// This fixture is emitted and checked against actual API/collector/persistence
// responses by test_provider_account_usage_delivery.py, not authored UI quota data.
describe('two-provider account usage delivery contract', () => {
  it('delivers four isolated provider accounts through cached refresh without losing an editor draft', async () => {
    const initial = deliveryFixture.initial as unknown as ProviderProfile[];
    const refreshed = deliveryFixture.refreshed as unknown as ProviderProfile[];
    const harness = renderLiveManager(initial, {
      respond: () => deliveryFixture.refresh.results as RefreshResult[],
      nextList: () => refreshed,
    });
    fireEvent.click(profileAction('Edit', 'claude-a'));
    const label = screen.getByLabelText(/Account label/) as HTMLInputElement;
    fireEvent.change(label, { target: { value: 'Unsaved Claude account label' } });
    label.focus();

    await advance(10);
    expect(harness.refreshRequests).toHaveLength(1);
    expect([...harness.refreshRequests[0]!.body.profile_ids].sort()).toEqual([
      'claude-a', 'claude-b', 'codex-a', 'codex-b',
    ]);
    await advance(2_000);
    expect(harness.listFetches()).toBe(1);
    for (const profile of refreshed) {
      const group = usageGroup(profile.profile_id);
      const usage = profile.account_usage!;
      expect(text(group)).toContain(usage.account.email!);
      expect(text(group)).toContain(usage.account.display_name!);
      const primary = (usage.usage.windows ?? []).find((window) => window.key === usage.usage.primary_window_key)!;
      expect(within(group).getByRole('meter', { name: 'Weekly allowance remaining' }).getAttribute('aria-valuenow'))
        .toBe(String(primary.remaining_percent));
      expect(text(group)).toContain('Resets');
      expect(text(group)).toContain('Updated');
      expect(within(group).queryByRole('status')).toBeNull();
    }
    expect(refreshed.filter((profile) => profile.provider_id === 'openai')).toHaveLength(2);
    expect(refreshed.filter((profile) => profile.provider_id === 'anthropic')).toHaveLength(2);
    expect((screen.getByLabelText(/Account label/) as HTMLInputElement).value).toBe('Unsaved Claude account label');
    expect(document.activeElement).toBe(screen.getByLabelText(/Account label/));
    expect(within(screen.getByRole('region', { name: 'Account & usage' })).getByRole('meter')).toBeTruthy();
    expect(harness.allRequests.filter((request) => request.method !== 'GET' && request.url !== ACCOUNT_USAGE_REFRESH_PATH)).toEqual([]);
  });

  it('renders the fenced replacement account while ignoring a late old-generation refresh reply', async () => {
    let answer!: (results: RefreshResult[]) => void;
    const refreshed = deliveryFixture.refreshed as unknown as ProviderProfile[];
    const rebound = deliveryFixture.rebound as unknown as ProviderProfile[];
    const harness = renderLiveManager(refreshed, {
      respond: () => new Promise((resolve) => (answer = resolve)),
    });
    await advance(10);
    fireEvent.click(within(usageGroup('codex-a')).getByRole('button', {
      name: 'Refresh account usage for codex-a',
    }));
    await advance(10);
    expect(harness.refreshRequests).toHaveLength(1);
    harness.setProfiles(rebound);
    await advance(10);
    await act(async () => {
      answer(deliveryFixture.refresh.results.filter((result) => result.profile_id === 'codex-a') as RefreshResult[]);
    });
    await advance(10_000);
    const replacement = rebound.find((profile) => profile.profile_id === 'codex-a')!.account_usage!;
    expect(text(usageGroup('codex-a'))).toContain(replacement.account.email!);
    expect(within(usageGroup('codex-a')).queryByRole('status')).toBeNull();
    expect(harness.listFetches()).toBe(0);
    expect(text(usageGroup('claude-a'))).toContain(rebound.find((profile) => profile.profile_id === 'claude-a')!.account_usage!.account.email!);
    for (const id of ['codex-b', 'claude-b']) {
      expect(text(usageGroup(id))).toContain(refreshed.find((profile) => profile.profile_id === id)!.account_usage!.account.display_name!);
    }
  });
});
