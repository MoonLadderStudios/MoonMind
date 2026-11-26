import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { components } from '../../generated/openapi';
import {
  formatRemainingPercent,
  formatResetTime,
  primaryWeeklyWindow,
  ProviderAccountEmail,
  ProviderProfileAccountUsage,
  type AccountUsageRefreshView,
} from './ProviderProfileAccountUsage';
import { accountUsageProjection, usageWindow } from './accountUsageTestFixtures';

type Projection = components['schemas']['ProviderAccountUsageProjection'];

const NOW = Date.parse('2026-11-18T20:00:00Z');
const RESET = '2026-11-20T21:00:00Z';
const projection = accountUsageProjection;

function renderUsage(
  usage: Projection | null | undefined,
  options: {
    oauth?: boolean;
    variant?: 'row' | 'full';
    refresh?: AccountUsageRefreshView;
    onRefresh?: () => void;
    reconnect?: { label: string; onSelect: () => void; disabledReason?: string | null };
  } = {},
) {
  const props = {
    subject: 'codex-oauth',
    usage,
    oauth: options.oauth ?? true,
    refresh: options.refresh,
    onRefresh: options.onRefresh ?? (() => undefined),
    reconnect: options.reconnect ?? null,
  };
  if (options.variant === 'full') return render(<ProviderProfileAccountUsage {...props} variant="full" />);
  // A saved-profile row: the reading in the row, everything else in its details.
  return render(
    <div data-testid="usage-row">
      <ProviderProfileAccountUsage {...props} variant="summary" />
      <ProviderProfileAccountUsage {...props} variant="details" />
    </div>,
  );
}

/** The editor block, or a saved-profile row's summary and details together. */
function usageRegion(): HTMLElement {
  return (
    screen.queryByTestId('usage-row') ??
    screen.getByRole('group', { name: 'Account and usage for codex-oauth' })
  );
}

function usageDetails(): HTMLElement {
  return screen.getByRole('group', { name: 'Account and usage details for codex-oauth' });
}

function text(element: HTMLElement): string {
  return (element.textContent ?? '').replace(/\s+/g, ' ');
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('formatResetTime', () => {
  const format = (iso: string, timeZone: string) =>
    formatResetTime(iso, { locale: 'en-US', timeZone }).replace(/\s/g, ' ');

  it('renders weekday, date, local time and zone in Portland', () => {
    expect(format(RESET, 'America/Los_Angeles')).toBe('Friday, Nov 20 at 1:00 PM PST');
  });

  it('derives the local calendar day across the UTC date boundary', () => {
    const lateFriday = '2026-11-21T03:30:00Z';
    expect(format(lateFriday, 'America/Los_Angeles')).toBe('Friday, Nov 20 at 7:30 PM PST');
    expect(format(lateFriday, 'UTC')).toBe('Saturday, Nov 21 at 3:30 AM UTC');
  });

  it('renders another time zone from the same instant', () => {
    expect(format(RESET, 'Asia/Tokyo')).toBe('Saturday, Nov 21 at 6:00 AM GMT+9');
  });

  it('follows daylight saving changes instead of a fixed offset', () => {
    // US DST ends 2026-11-01: the same 1 PM local reset moves by an hour in UTC.
    expect(format('2026-10-30T20:00:00Z', 'America/Los_Angeles')).toBe('Friday, Oct 30 at 1:00 PM PDT');
    expect(format('2026-11-06T21:00:00Z', 'America/Los_Angeles')).toBe('Friday, Nov 6 at 1:00 PM PST');
    // UK DST ends 2026-10-25.
    expect(format('2026-10-23T12:00:00Z', 'Europe/London')).toBe('Friday, Oct 23 at 1:00 PM GMT+1');
    expect(format('2026-10-30T13:00:00Z', 'Europe/London')).toBe('Friday, Oct 30 at 1:00 PM GMT');
  });
});

describe('formatRemainingPercent', () => {
  it('never overstates remaining allowance', () => {
    expect(formatRemainingPercent(36)).toBe('36%');
    expect(formatRemainingPercent(99.6)).toBe('99%');
    expect(formatRemainingPercent(0.4)).toBe('<1%');
    expect(formatRemainingPercent(0)).toBe('0%');
    expect(formatRemainingPercent(100)).toBe('100%');
  });
});

describe('primaryWeeklyWindow', () => {
  it('never promotes a model-scoped or short window to the main weekly allowance', () => {
    const modelWeekly = usageWindow({ key: 'model.weekly', scope: 'model', model_label: 'Reported Model X' });
    const short = usageWindow({ key: 'five_hour', period: 'short', duration_seconds: 18000 });
    expect(primaryWeeklyWindow(projection({ windows: [modelWeekly, short], primaryWindowKey: null }).usage)).toBeNull();
    expect(
      primaryWeeklyWindow(projection({ windows: [short, usageWindow()], primaryWindowKey: null }).usage)?.key,
    ).toBe('weekly');
  });
});

describe('ProviderProfileAccountUsage', () => {
  it('shows account, prominent weekly remaining, local reset, freshness and actions', () => {
    const onRefresh = vi.fn();
    renderUsage(projection(), { onRefresh });

    const region = usageRegion();
    expect(text(region)).toContain('personal@example.com');
    expect(text(region)).toContain('Personal workspace');
    expect(within(region).getByText('36% remaining')).toBeTruthy();
    const meter = within(region).getByRole('meter', { name: 'Weekly allowance remaining' });
    expect(meter.getAttribute('aria-valuenow')).toBe('36');
    expect(meter.getAttribute('aria-valuetext')).toBe('36% remaining');
    expect(text(region)).toContain(`Resets ${formatResetTime(RESET).replace(/\s+/g, ' ')}`);
    expect(text(region)).toContain('Updated 2 minutes ago');

    const refresh = within(region).getByRole('button', { name: 'Refresh account usage for codex-oauth' });
    expect(refresh.getAttribute('type')).toBe('button');
    fireEvent.click(refresh);
    expect(onRefresh).toHaveBeenCalledTimes(1);
    // The row reading carries the allowance; the account sits in its details.
    expect(text(usageDetails())).toContain('personal@example.com');
    // A current reading needs no age in the row.
    expect(text(screen.getByRole('group', { name: 'Account and usage for codex-oauth' }))).not.toContain('Updated');
    expect(text(screen.getByRole('group', { name: 'Account and usage for codex-oauth' }))).not.toContain(
      'personal@example.com',
    );
  });

  it('keeps short-window and model-specific weekly limits in separate groups with reported labels', () => {
    renderUsage(
      projection({
        windows: [
          usageWindow(),
          usageWindow({
            key: 'five_hour',
            period: 'short',
            duration_seconds: 18000,
            used_percent: 10,
            remaining_percent: 90,
            reset_at: '2026-11-18T22:00:00Z',
          }),
          usageWindow({
            key: 'model.reported',
            scope: 'model',
            model_label: 'Reported Model X (beta)',
            used_percent: 20,
            remaining_percent: 80,
          }),
          usageWindow({
            key: 'context',
            period: 'other',
            duration_seconds: null,
            used_percent: 45,
            remaining_percent: 55,
            reset_at: null,
          }),
        ],
      }),
    );

    const region = usageRegion();
    expect(within(region).getByText('36% remaining')).toBeTruthy();
    const details = usageDetails();
    const modelGroup = within(details).getByRole('list', { name: 'Model-specific weekly limits' });
    expect(text(modelGroup)).toContain('Reported Model X (beta)');
    expect(text(modelGroup)).toContain('80% remaining');
    const shortGroup = within(details).getByRole('list', { name: 'Short-window limits' });
    expect(text(shortGroup)).toContain('5-hour window');
    expect(text(shortGroup)).toContain('90% remaining');
    const otherGroup = within(details).getByRole('list', { name: 'Other reported limits' });
    expect(text(otherGroup)).toContain('context');
    // Context-style windows never become the weekly allowance.
    expect(within(details).getByRole('list', { name: 'Weekly allowance' }).textContent).not.toContain('55%');
  });

  it('says the weekly allowance is unreported instead of using a model-specific number', () => {
    renderUsage(
      projection({
        windows: [
          usageWindow({ key: 'model.reported', scope: 'model', model_label: 'Reported Model X', remaining_percent: 80, used_percent: 20 }),
        ],
        primaryWindowKey: null,
      }),
    );

    const region = usageRegion();
    expect(text(region)).toContain('Weekly usage not reported');
    expect(within(region).queryByRole('meter')).toBeNull();
    expect(within(region).queryByText('80% remaining')).toBeNull();
  });

  it('reports a missing weekly window and missing email without fabricating values', () => {
    renderUsage(projection({ status: 'not_reported', windows: [], account: { email: null, display_name: null } }));

    const region = usageRegion();
    expect(text(region)).toContain('Email unavailable');
    expect(text(region)).toContain('Weekly usage not reported');
    expect(text(region)).not.toMatch(/\d+% remaining/);
  });

  it('shows usage with an unavailable email and an unavailable reset time', () => {
    renderUsage(projection({ account: { email: null }, windows: [usageWindow({ reset_at: null })] }));

    const region = usageRegion();
    expect(text(region)).toContain('Email unavailable');
    expect(within(region).getByText('36% remaining')).toBeTruthy();
    expect(text(region)).toContain('Reset time unavailable');
  });

  it('shows initial collection as checking', () => {
    renderUsage(
      projection({
        status: 'unknown',
        windows: [],
        account: { status: 'unknown', email: null, display_name: null, plan: null, observed_at: null },
        refresh: { state: 'queued', last_success_at: null, last_attempt_at: null },
      }),
    );

    expect(text(usageRegion())).toContain('Checking account and usage…');
    expect(text(usageRegion())).not.toContain('Email unavailable');
  });

  it('keeps last-known values and their real age through a temporary failure', () => {
    vi.setSystemTime(Date.parse('2026-11-18T20:40:00Z'));
    renderUsage(
      projection({
        status: 'unavailable',
        windows: [usageWindow({ freshness: 'stale', last_known: true })],
        refresh: { last_failure: 'timeout', last_failure_at: '2026-11-18T20:39:00Z' },
      }),
    );

    const region = usageRegion();
    expect(within(region).getByText('36% remaining')).toBeTruthy();
    expect(text(region)).toContain('Updated 42 minutes ago');
    expect(text(region)).toContain('Last known');
    expect(text(region)).toContain('Usage temporarily unavailable: the provider did not respond in time.');
    // The collapsed row itself marks the reading as outdated.
    expect(text(screen.getByRole('group', { name: 'Account and usage for codex-oauth' }))).toContain(
      'Updated 42 minutes ago · Last known',
    );
  });

  it('marks a plainly stale weekly reading as last known in the row', () => {
    vi.setSystemTime(Date.parse('2026-11-18T20:40:00Z'));
    renderUsage(projection({ status: 'stale' }));

    const row = screen.getByRole('group', { name: 'Account and usage for codex-oauth' });
    expect(within(row).getByText('36% remaining')).toBeTruthy();
    expect(text(row)).toContain('Last known');
  });

  it('gives update or reconnect guidance when idle sign-in renewal is unsupported', () => {
    renderUsage(
      projection({
        status: 'unavailable',
        windows: [usageWindow({ freshness: 'stale', last_known: true })],
        refresh: { last_failure: 'credential_recovery_unsupported', last_failure_at: '2026-11-18T20:39:00Z' },
      }),
    );

    expect(text(usageRegion())).toContain(
      'Usage temporarily unavailable: the installed runtime cannot renew the stored sign-in while idle; update MoonMind or reconnect this profile.',
    );
  });

  it('reports an unavailable first reading without inventing an empty one', () => {
    renderUsage(
      projection({
        status: 'unavailable',
        windows: [],
        account: { status: 'unknown', email: null, display_name: null, plan: null, observed_at: null },
        refresh: { last_success_at: null, last_failure: 'rate_limited', last_failure_at: '2026-11-18T19:59:00Z' },
      }),
    );

    const region = usageRegion();
    expect(text(region)).toContain('Usage temporarily unavailable: the provider is limiting requests.');
    expect(text(region)).not.toContain('Weekly usage not reported');
    expect(text(region)).not.toContain('Email unavailable');
  });

  it('indicates an accepted refresh without claiming the values changed', () => {
    renderUsage(projection(), { refresh: { checking: true } });

    const region = usageRegion();
    expect(within(region).getByRole('status').textContent).toBe('Checking for updated usage…');
    expect(text(region)).toContain('Updated 2 minutes ago');
    expect(within(region).getByText('36% remaining')).toBeTruthy();
    const refresh = within(region).getByRole('button', { name: 'Refresh account usage for codex-oauth' });
    expect((refresh as HTMLButtonElement).disabled).toBe(true);
  });

  it('explains a busy-owner deferral without a spinner', () => {
    renderUsage(projection({ refresh: { state: 'deferred', request_id: 'req-1' } }), {
      refresh: { checking: false, outcome: 'deferred_busy', retryAtMs: NOW + 60_000 },
    });

    const region = usageRegion();
    expect(text(region)).toContain('This profile is busy, so the usage check was deferred');
    expect(within(region).queryByRole('status')).toBeNull();
    expect(within(region).getByText('36% remaining')).toBeTruthy();
  });

  it('shows when a provider backoff allows the next refresh and honors it', () => {
    const onRefresh = vi.fn();
    renderUsage(
      projection({ refresh: { state: 'backoff', next_allowed_at: '2026-11-18T20:03:00Z' } }),
      { onRefresh },
    );

    const region = usageRegion();
    expect(text(region)).toContain('Refresh available in 3 min');
    const refresh = within(region).getByRole('button', { name: 'Refresh account usage for codex-oauth' });
    expect((refresh as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(refresh);
    expect(onRefresh).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(3 * 60_000);
    });
    expect((refresh as HTMLButtonElement).disabled).toBe(false);
  });

  it('distinguishes missing usage access from a signed-out profile', () => {
    renderUsage(projection({ status: 'usage_access_missing', windows: [] }));

    const region = usageRegion();
    expect(text(region)).toContain('Usage access unavailable for this connection');
    expect(text(region)).toContain('personal@example.com');
    expect(text(region)).not.toMatch(/log(ged)? out|signed out/i);
  });

  it('routes expired logins through the existing connection flow', () => {
    const onSelect = vi.fn();
    renderUsage(projection({ status: 'reconnect_required' }), {
      reconnect: { label: 'Reconnect OAuth', onSelect },
    });

    const region = usageRegion();
    expect(text(region)).toContain('Sign-in expired or was revoked. Reconnect this profile to resume account and usage updates.');
    fireEvent.click(within(region).getByRole('button', { name: 'Reconnect OAuth' }));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('marks a passed reset as checking and never shows a replenished allowance', () => {
    vi.setSystemTime(Date.parse('2026-11-20T21:05:00Z'));
    renderUsage(projection({ status: 'stale', windows: [usageWindow({ freshness: 'expired', last_known: true })] }));

    const region = usageRegion();
    expect(text(region)).toContain('Reset time passed; checking…');
    expect(text(region)).toContain('Last known before reset: 36% remaining');
    expect(text(region)).not.toContain('100%');
    expect(within(region).queryByRole('meter')).toBeNull();
  });

  it('treats a reset that passes while the page is open as expired', () => {
    renderUsage(projection({ windows: [usageWindow({ reset_at: '2026-11-18T20:01:00Z' })] }));
    expect(screen.getByText('36% remaining')).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(2 * 60_000);
    });
    expect(text(usageRegion())).toContain('Reset time passed; checking…');
  });

  it('labels a depleted included allowance without treating it as a connection failure', () => {
    renderUsage(
      projection({ windows: [usageWindow({ used_percent: 112, remaining_percent: 0, exhausted: true })] }),
    );

    const region = usageRegion();
    expect(within(region).getByText('0% remaining')).toBeTruthy();
    expect(text(region)).toContain('Included weekly allowance used up');
    expect(text(region)).not.toMatch(/not connected|reconnect/i);
  });

  it('chooses unsupported copy by authentication method', () => {
    const { unmount } = renderUsage(projection({ status: 'not_supported', windows: [] }), { oauth: false });
    expect(text(usageRegion())).toContain(
      'Subscription weekly usage not available for this authentication method',
    );
    expect(screen.queryByRole('button', { name: /Refresh account usage/ })).toBeNull();
    unmount();

    renderUsage(projection({ status: 'not_supported', windows: [] }), { oauth: true });
    expect(text(usageRegion())).toContain('Account and usage reporting is not available for this provider yet');
    expect(text(usageRegion())).not.toContain('authentication method');
  });

  it('never gives an API-key profile OAuth recovery guidance', () => {
    // A failed API-key validation projects reconnect_required before support is checked.
    renderUsage(projection({ status: 'reconnect_required' }), {
      oauth: false,
      reconnect: { label: 'Reconnect OAuth', onSelect: () => undefined },
    });

    const region = usageRegion();
    expect(text(region)).toContain('Subscription weekly usage not available for this authentication method');
    expect(text(region)).not.toMatch(/sign-in|reconnect/i);
    expect(within(region).queryByRole('button')).toBeNull();
  });

  it('replaces the previous account display as soon as the credential generation changes', () => {
    const { rerender } = renderUsage(projection(), { refresh: { checking: false, outcome: 'backoff', retryAtMs: NOW + 60_000 } });
    expect(text(usageRegion())).toContain('personal@example.com');

    rerender(
      <ProviderProfileAccountUsage
        subject="codex-oauth"
        usage={projection({
          credential_generation: 4,
          snapshot_revision: 0,
          status: 'unknown',
          windows: [],
          account: { status: 'unknown', email: null, display_name: null, plan: null, observed_at: null },
          refresh: { last_success_at: null, last_attempt_at: null },
        })}
        oauth
        variant="summary"
        refresh={undefined}
        onRefresh={() => undefined}
        reconnect={null}
      />,
    );

    expect(text(usageRegion())).not.toContain('personal@example.com');
    expect(text(usageRegion())).toContain('Checking account and usage…');
  });

  it('advances relative age and countdown text without any new observation', () => {
    const onRefresh = vi.fn();
    renderUsage(projection(), { variant: 'full', onRefresh });
    expect(text(usageRegion())).toContain('Updated 2 minutes ago');
    expect(text(usageRegion())).toContain('in 2 days 1 hr');
    expect(text(usageRegion())).toContain('2026-11-20T21:00:00Z');

    act(() => {
      vi.advanceTimersByTime(3 * 60_000);
    });
    expect(text(usageRegion())).toContain('Updated 5 minutes ago');
    expect(text(usageRegion())).toContain('Last known');
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('wraps long emails and limit names instead of overflowing actions', () => {
    const email = `${'very.long.mailbox.name.'.repeat(6)}person@an-extremely-long-workspace-domain.example.com`;
    renderUsage(projection({ account: { email } }));

    const emailText = screen.getByText(email);
    expect(emailText.className).toContain('[overflow-wrap:anywhere]');
    expect(usageDetails().className).toContain('min-w-0');
  });

  it('renders nothing when the backend did not project account usage', () => {
    const { container } = renderUsage(undefined);
    expect(container.textContent).toBe('');
  });
});

describe('ProviderAccountEmail', () => {
  function renderEmail(usage: Projection | null | undefined, oauth = true) {
    return render(<ProviderAccountEmail usage={usage} oauth={oauth} />);
  }

  it('shows the provider-reported account email for a subscription login', () => {
    const { container } = renderEmail(projection());

    const email = screen.getByText('personal@example.com');
    expect(email.className).toContain('[overflow-wrap:anywhere]');
    expect(container.textContent).toBe('Provider account: personal@example.com');
  });

  it('keeps showing the last-known email while sign-in must be renewed', () => {
    renderEmail(projection({ status: 'reconnect_required' }));
    expect(screen.getByText('personal@example.com')).toBeTruthy();
  });

  it('says the email is unavailable when the provider reported the account without one', () => {
    const { container } = renderEmail(
      projection({ status: 'not_reported', windows: [], account: { email: null, display_name: null } }),
    );
    expect(container.textContent).toBe('Provider account: Email unavailable');
  });

  it.each([
    ['an API-key profile', projection({ status: 'not_supported', windows: [] }), false],
    ['an unsupported provider', projection({ status: 'not_supported', windows: [], account: { email: null } }), true],
    [
      'a profile not yet checked',
      projection({
        status: 'unknown',
        windows: [],
        account: { status: 'unknown', email: null, display_name: null, plan: null, observed_at: null },
      }),
      true,
    ],
    ['a failed projection', null, true],
    ['a profile without a projection', undefined, true],
  ])('renders nothing for %s', (_label, usage, oauth) => {
    const { container } = renderEmail(usage, oauth);
    expect(container.textContent).toBe('');
  });
});
