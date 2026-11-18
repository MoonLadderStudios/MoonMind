import { Button } from '../Button';
import type { components } from '../../generated/openapi';
import { useCoarseClock } from '../../lib/liveClock';

// The provider-reported account and allowance for one Provider Profile, read
// from MoonMind's cached `account_usage` projection. This is an observation,
// never profile configuration: it is rendered beside the profile and never
// copied into the editor's form state.

export type ProviderAccountUsage = components['schemas']['ProviderAccountUsageProjection'];
type UsageSummary = components['schemas']['ProviderAccountUsageSummary'];
type UsageWindow = components['schemas']['ProviderAccountUsageWindow'];
type RefreshOutcome = components['schemas']['RefreshOutcome'];
type FailureCode = components['schemas']['AccountUsageFailureCode'];

/** Client-side refresh state for one profile's current credential generation. */
export interface AccountUsageRefreshView {
  /** A refresh request is in flight or an accepted attempt is being awaited. */
  checking: boolean;
  /** The latest refresh outcome the backend returned for this generation. */
  outcome?: RefreshOutcome | null | undefined;
  /** Earliest time (epoch ms) the backend allows another refresh request. */
  retryAtMs?: number | null | undefined;
  /** Waiting for an accepted attempt ended before a new observation arrived. */
  timedOut?: boolean | undefined;
  /** The refresh request itself failed before the backend answered. */
  requestFailed?: boolean | undefined;
}

export interface AccountUsageReconnectAction {
  label: string;
  onSelect: () => void;
  disabledReason?: string | null | undefined;
}

interface ProviderProfileAccountUsageProps {
  /** Profile ID, used for accessible names. */
  subject: string;
  /** `undefined` when the API did not project usage; `null` when it failed to. */
  usage: ProviderAccountUsage | null | undefined;
  /** Whether the profile authenticates with a provider subscription login. */
  oauth: boolean;
  /**
   * `summary` is the saved-profile row reading, `details` the account and
   * every reported window for the row's expanded panel, `full` the editor.
   */
  variant: 'summary' | 'details' | 'full';
  refresh?: AccountUsageRefreshView | undefined;
  onRefresh?: (() => void) | undefined;
  /** The existing connection-flow action, offered when sign-in must be renewed. */
  reconnect?: AccountUsageReconnectAction | null | undefined;
}

export interface DisplayTimeOptions {
  locale?: string | undefined;
  timeZone?: string | undefined;
}

const IN_FLIGHT_STATES = new Set(['queued', 'running']);

const REFRESH_ICON = (
  <svg aria-hidden="true" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" focusable="false">
    <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" />
    <path d="M13.5 2.5v2.75h-2.75" />
  </svg>
);

const FAILURE_REASONS: Record<FailureCode, string> = {
  timeout: 'the provider did not respond in time',
  rate_limited: 'the provider is limiting requests',
  provider_unavailable: 'the provider is unavailable',
  malformed_response: 'the provider returned a response MoonMind could not read',
  usage_access_missing: 'this connection does not grant usage access',
  reconnect_required: 'the sign-in must be renewed',
  dispatch_failed: 'the usage check could not start',
  credential_refresh_unavailable: 'the stored sign-in could not be renewed while idle',
  credential_recovery_unsupported:
    'the installed runtime cannot renew the stored sign-in while idle; update MoonMind or reconnect this profile',
  attempt_abandoned: 'the usage check did not finish',
  internal_error: 'the usage check failed',
};

export function parseInstant(value: string | null | undefined): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * Local weekday, date, time and zone for a provider-reported UTC instant, in
 * the browser's locale and time zone unless overridden. Daylight saving comes
 * from the zone rules for that instant; nothing is inferred locally.
 */
export function formatResetTime(iso: string, options: DisplayTimeOptions = {}): string {
  const instant = new Date(iso);
  if (Number.isNaN(instant.getTime())) return '';
  const date = new Intl.DateTimeFormat(options.locale, {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
    timeZone: options.timeZone,
  }).format(instant);
  const time = new Intl.DateTimeFormat(options.locale, {
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
    timeZone: options.timeZone,
  }).format(instant);
  return `${date} at ${time}`;
}

function formatClockTime(ms: number): string {
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(new Date(ms));
}

function formatExactInstant(iso: string): string {
  const ms = parseInstant(iso);
  return ms === null ? iso : new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** Remaining allowance, rounded down so it is never overstated. */
export function formatRemainingPercent(value: number): string {
  if (value <= 0) return '0%';
  if (value < 1) return '<1%';
  return `${Math.floor(Math.min(100, value))}%`;
}

function plural(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? '' : 's'}`;
}

function formatAge(ms: number): string {
  const minutes = Math.floor(Math.max(0, ms) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${plural(minutes, 'minute')} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${plural(hours, 'hour')} ago`;
  return `${plural(Math.floor(hours / 24), 'day')} ago`;
}

function formatCountdown(ms: number): string {
  const totalMinutes = Math.max(1, Math.ceil(ms / 60_000));
  if (totalMinutes < 60) return `in ${totalMinutes} min`;
  const totalHours = Math.floor(totalMinutes / 60);
  if (totalHours < 24) {
    const minutes = totalMinutes % 60;
    return minutes ? `in ${totalHours} hr ${minutes} min` : `in ${totalHours} hr`;
  }
  const hours = totalHours % 24;
  const days = plural(Math.floor(totalHours / 24), 'day');
  return hours ? `in ${days} ${hours} hr` : `in ${days}`;
}

/** The general weekly allowance; model-scoped and short windows never qualify. */
export function primaryWeeklyWindow(usage: UsageSummary | null | undefined): UsageWindow | null {
  const windows = usage?.windows ?? [];
  const isGeneralWeekly = (window: UsageWindow) =>
    window.period === 'weekly' && window.scope === 'general';
  if (usage?.primary_window_key) {
    const primary = windows.find((window) => window.key === usage.primary_window_key);
    if (primary && isGeneralWeekly(primary)) return primary;
  }
  const generalWeekly = windows.filter(isGeneralWeekly);
  return generalWeekly.length === 1 ? generalWeekly[0]! : null;
}

function resetPassed(window: UsageWindow, nowMs: number): boolean {
  const reset = parseInstant(window.reset_at);
  return window.freshness === 'expired' || (reset !== null && reset <= nowMs);
}

function isLastKnown(window: UsageWindow, nowMs: number): boolean {
  const freshUntil = parseInstant(window.fresh_until);
  return (
    window.last_known ||
    window.freshness !== 'fresh' ||
    resetPassed(window, nowMs) ||
    (freshUntil !== null && freshUntil <= nowMs)
  );
}

function windowLabel(window: UsageWindow): string {
  if (window.period === 'weekly' && window.scope === 'general') return 'General weekly allowance';
  const hours = window.duration_seconds ? window.duration_seconds / 3600 : null;
  const duration =
    window.period === 'short'
      ? hours !== null && Number.isInteger(hours)
        ? `${hours}-hour window`
        : 'Short window'
      : null;
  const reported = window.model_label ?? (window.period === 'other' ? window.key : null);
  return [reported, duration].filter(Boolean).join(' · ') || window.key;
}

function remainingText(window: UsageWindow): string {
  return window.remaining_percent == null
    ? 'Remaining not reported'
    : `${formatRemainingPercent(window.remaining_percent)} remaining`;
}

function latestObservation(usage: ProviderAccountUsage, primary: UsageWindow | null): number | null {
  if (primary) return parseInstant(primary.observed_at);
  const times = (usage.usage.windows ?? [])
    .map((window) => parseInstant(window.observed_at))
    .filter((value): value is number => value !== null);
  return times.length ? Math.max(...times) : parseInstant(usage.account.observed_at);
}

function WindowList({ label, windows, nowMs }: { label: string; windows: UsageWindow[]; nowMs: number }) {
  if (windows.length === 0) return null;
  return (
    <div className="space-y-1">
      <div className="font-semibold text-slate-700 dark:text-slate-300">{label}</div>
      <ul aria-label={label} className="space-y-1">
        {windows.map((window) => {
          const passed = resetPassed(window, nowMs);
          const observed = parseInstant(window.observed_at);
          return (
            <li key={window.key} className="min-w-0 [overflow-wrap:anywhere]">
              <span className="font-medium">{windowLabel(window)}</span>
              {': '}
              {passed ? `Last known before reset: ${remainingText(window)}` : remainingText(window)}
              {window.used_percent != null && window.used_percent > 100
                ? ` (used ${Math.round(window.used_percent)}%)`
                : null}
              {window.reset_at ? (
                <>
                  {' · '}
                  {passed ? 'Reset passed' : `Resets ${formatResetTime(window.reset_at)}`}
                  {' '}
                  <span className="font-mono">({formatExactInstant(window.reset_at)})</span>
                </>
              ) : (
                ' · Reset time unavailable'
              )}
              {observed !== null ? ` · Observed ${formatAge(nowMs - observed)}` : null}
              {isLastKnown(window, nowMs) ? ' · Last known' : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ProviderProfileAccountUsage({
  subject,
  usage,
  oauth,
  variant,
  refresh,
  onRefresh,
  reconnect,
}: ProviderProfileAccountUsageProps) {
  // Presentation-only clock: ticking text never touches observation times.
  const nowMs = useCoarseClock(Boolean(usage));
  if (usage === undefined) return null;

  const full = variant === 'full';
  const summary = variant === 'summary';
  const detailsOnly = variant === 'details';
  const frameClass = full
    ? 'min-w-0 max-w-full space-y-3 text-sm text-slate-700 dark:text-slate-300'
    : 'min-w-0 max-w-full space-y-1 text-xs text-slate-700 dark:text-slate-300 [&_p]:my-0';
  const label = detailsOnly ? `Account and usage details for ${subject}` : `Account and usage for ${subject}`;
  const muted = 'text-slate-500 dark:text-slate-400';

  // Only subscription logins have weekly usage, whatever status is projected.
  if (!oauth || usage === null || usage.status === 'not_supported') {
    const message = !oauth
      ? 'Subscription weekly usage not available for this authentication method.'
      : usage === null
        ? 'Account and usage are unavailable right now.'
        : 'Account and usage reporting is not available for this provider yet.';
    return (
      <div role="group" aria-label={label} className={frameClass}>
        <p className={muted}>
          {detailsOnly ? 'No account or usage details are reported for this profile.' : message}
        </p>
      </div>
    );
  }

  const windows = usage.usage.windows ?? [];
  const primary = primaryWeeklyWindow(usage.usage);
  const modelWeekly = windows.filter((w) => w.period === 'weekly' && w.scope === 'model');
  const shortWindows = windows.filter((w) => w.period === 'short');
  const otherWindows = windows.filter(
    (w) => w !== primary && !modelWeekly.includes(w) && !shortWindows.includes(w),
  );
  const account = usage.account;
  const hasData = windows.length > 0 || Boolean(account.email || account.display_name);
  const backendChecking = IN_FLIGHT_STATES.has(usage.refresh.state);
  const checking = Boolean(refresh?.checking) || backendChecking;
  const initial = !hasData && (usage.status === 'unknown' || checking);
  // Without any reading, only a confirmed empty report can say "not reported".
  const showReading = hasData || usage.status === 'not_reported';
  const observedMs = latestObservation(usage, primary);
  const stale = usage.status === 'stale' || usage.status === 'unavailable' || (primary !== null && isLastKnown(primary, nowMs));
  const primaryPassed = primary !== null && resetPassed(primary, nowMs);
  const retryAtMs = Math.max(parseInstant(usage.refresh.next_allowed_at) ?? 0, refresh?.retryAtMs ?? 0);
  const waitingForRetry = retryAtMs > nowMs;
  const deferred = usage.refresh.state === 'deferred' || refresh?.outcome === 'deferred_busy';

  const notices: string[] = [];
  if (usage.status === 'reconnect_required') {
    notices.push('Sign-in expired or was revoked. Reconnect this profile to resume account and usage updates.');
  } else if (usage.status === 'usage_access_missing') {
    notices.push(
      'Usage access unavailable for this connection. The profile is still connected; its sign-in does not grant usage reporting.',
    );
  } else if (usage.status === 'unavailable') {
    const reason = usage.refresh.last_failure ? FAILURE_REASONS[usage.refresh.last_failure] : null;
    notices.push(
      `Usage temporarily unavailable${reason ? `: ${reason}` : ''}.${hasData ? ' Showing last-known values.' : ''}`,
    );
  }
  if (deferred && !checking) {
    notices.push(
      `This profile is busy, so the usage check was deferred${
        waitingForRetry ? ` until ${formatClockTime(retryAtMs)}` : ''
      }.${hasData ? ' Showing cached values.' : ''}`,
    );
  } else if (waitingForRetry && !checking) {
    notices.push(`Refresh available ${formatCountdown(retryAtMs - nowMs)} (${formatClockTime(retryAtMs)}).`);
  }
  if (refresh?.outcome === 'not_connected') notices.push('Connect this profile to check its usage.');
  if (refresh?.outcome === 'disabled') notices.push('Enable this profile to check its usage.');
  if (refresh?.timedOut && !checking) {
    notices.push('The usage check is taking longer than expected; showing the last-known values.');
  }
  if (refresh?.requestFailed && !checking) notices.push('The refresh request failed. Try again.');

  const refreshDisabledReason = checking
    ? 'A usage check is already running.'
    : deferred && waitingForRetry
      ? 'The profile is busy; MoonMind will check again shortly.'
      : waitingForRetry
        ? `Available ${formatCountdown(retryAtMs - nowMs)}.`
        : null;

  const accountBlock = (
    <div className="min-w-0">
      <span className={muted}>Provider account: </span>
      {account.email ? (
        <span className="font-medium text-slate-900 dark:text-white [overflow-wrap:anywhere]">
          {account.email}
        </span>
      ) : (
        <span className={muted}>Email unavailable</span>
      )}
      {account.display_name ? (
        <span className={`block [overflow-wrap:anywhere] ${muted}`}>
          <span className="sr-only">Organization or workspace: </span>
          {account.display_name}
        </span>
      ) : null}
    </div>
  );
  const updatedLine =
    observedMs !== null ? (
      <p className={muted}>
        Updated {formatAge(nowMs - observedMs)}
        {stale ? ' · Last known' : null}
      </p>
    ) : null;
  const windowLists = (
    <div className="space-y-2">
      <WindowList label="Weekly allowance" windows={primary ? [primary] : []} nowMs={nowMs} />
      <WindowList label="Model-specific weekly limits" windows={modelWeekly} nowMs={nowMs} />
      <WindowList label="Short-window limits" windows={shortWindows} nowMs={nowMs} />
      <WindowList label="Other reported limits" windows={otherWindows} nowMs={nowMs} />
      {account.plan ? <p>Plan: {account.plan}</p> : null}
      {usage.refresh.last_failure && usage.refresh.last_failure_at ? (
        <p>
          Last failed check: {FAILURE_REASONS[usage.refresh.last_failure]} (
          {formatAge(nowMs - (parseInstant(usage.refresh.last_failure_at) ?? nowMs))})
        </p>
      ) : null}
    </div>
  );

  if (detailsOnly) {
    return (
      <div role="group" aria-label={label} className={`${frameClass} space-y-2`}>
        {showReading ? accountBlock : <p className={muted}>No account or usage reported yet.</p>}
        {showReading ? updatedLine : null}
        {hasData ? windowLists : null}
      </div>
    );
  }

  const refreshButton = onRefresh ? (
    <Button
      variant={summary ? 'icon' : 'secondary'}
      size="sm"
      className={summary ? 'provider-usage-refresh shrink-0' : undefined}
      aria-label={`Refresh account usage for ${subject}`}
      onClick={onRefresh}
      disabled={refreshDisabledReason !== null}
      disabledReason={refreshDisabledReason}
    >
      {summary ? REFRESH_ICON : 'Refresh'}
    </Button>
  ) : null;
  const reconnectButton =
    usage.status === 'reconnect_required' && reconnect ? (
      <Button
        variant="secondary"
        size="sm"
        onClick={reconnect.onSelect}
        disabled={Boolean(reconnect.disabledReason)}
        disabledReason={reconnect.disabledReason}
      >
        {reconnect.label}
      </Button>
    ) : null;

  const reading = initial ? (
    <p className="font-medium">Checking account and usage…</p>
  ) : showReading ? (
    <>
      {summary ? null : accountBlock}

      {primary === null ? (
        usage.status === 'usage_access_missing' || usage.status === 'reconnect_required' ? null : (
          <p className="font-medium">Weekly usage not reported</p>
        )
      ) : primaryPassed ? (
        <div>
          <p className="font-medium">Reset time passed; checking…</p>
          <p className={muted}>Last known before reset: {remainingText(primary)}</p>
        </div>
      ) : (
        <div className="space-y-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className={`${full ? 'text-xl' : 'text-sm'} font-semibold text-slate-900 dark:text-white`}>
              {remainingText(primary)}
            </span>
            {summary ? null : <span className={muted}>weekly allowance</span>}
          </div>
          {primary.remaining_percent != null ? (
            <div
              role="meter"
              aria-label="Weekly allowance remaining"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.floor(Math.min(100, Math.max(0, primary.remaining_percent)))}
              aria-valuetext={remainingText(primary)}
              className={`h-1.5 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700 ${summary ? '' : 'max-w-48'}`}
            >
              <div
                className="h-full rounded-full bg-emerald-600 dark:bg-emerald-400"
                style={{ width: `${Math.min(100, Math.max(0, primary.remaining_percent))}%` }}
              />
            </div>
          ) : null}
          {primary.exhausted ? <p className="font-medium">Included weekly allowance used up.</p> : null}
          <p className={summary ? muted : undefined}>
            {primary.reset_at ? (
              <>
                Resets {formatResetTime(primary.reset_at)}
                {full ? (
                  <span className={muted}>
                    {' '}
                    ({formatCountdown((parseInstant(primary.reset_at) ?? nowMs) - nowMs)};{' '}
                    <span className="font-mono">{formatExactInstant(primary.reset_at)}</span>)
                  </span>
                ) : null}
              </>
            ) : (
              'Reset time unavailable'
            )}
          </p>
        </div>
      )}

      {summary ? null : updatedLine}
    </>
  ) : null;

  const status = (
    <>
      {checking && !initial ? (
        <p role="status" className="font-medium">Checking for updated usage…</p>
      ) : null}
      {notices.map((notice) => (
        <p key={notice} className="[overflow-wrap:anywhere]">{notice}</p>
      ))}
    </>
  );

  if (summary) {
    // The row reading: allowance, reset and anything needing attention. The
    // account and every other reported window live in the row's details.
    return (
      <div role="group" aria-label={label} className={`${frameClass} flex items-start gap-2`}>
        <div className="min-w-0 flex-1 space-y-1">
          {reading}
          {status}
          {reconnectButton}
        </div>
        {refreshButton}
      </div>
    );
  }

  return (
    <div role="group" aria-label={label} className={frameClass}>
      {reading}
      {status}

      <div className="flex flex-wrap items-center gap-2">
        {reconnectButton}
        {refreshButton}
      </div>

      {hasData ? (
        <details className="min-w-0">
          <summary className="cursor-pointer font-medium text-slate-600 dark:text-slate-400">Usage details</summary>
          <div className="mt-2">{windowLists}</div>
        </details>
      ) : null}
    </div>
  );
}
