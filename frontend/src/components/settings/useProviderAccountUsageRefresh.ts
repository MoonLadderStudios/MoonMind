import { useEffect, useState } from 'react';

import type { components } from '../../generated/openapi';
import { responseError } from '../../lib/api/client';
import {
  parseInstant,
  type AccountUsageRefreshView,
  type ProviderAccountUsage,
} from './ProviderProfileAccountUsage';

type RefreshRequest = components['schemas']['ProviderAccountUsageRefreshRequest'];
type RefreshResponse = components['schemas']['ProviderAccountUsageRefreshResponse'];
type RefreshResult = components['schemas']['ProviderAccountUsageRefreshResult'];

export const ACCOUNT_USAGE_REFRESH_PATH = '/api/v1/provider-profiles/account-usage/refresh';

/** Visible Settings re-checks the displayed profiles about this often. */
const VISIBLE_CHECK_INTERVAL_MS = 5 * 60_000;
/** The backend's visibility staleness: five minutes after the last success. */
const STALE_AFTER_MS = 5 * 60_000;
/** Focus and visibility events never re-request one profile sooner than this. */
const VISIBILITY_REQUEST_GAP_MS = 60_000;
/** The refresh command accepts at most this many profiles per request. */
const MAX_PROFILES_PER_REQUEST = 50;
/** Cache re-reads while an accepted attempt is pending; they span the backend's two-minute attempt deadline. */
const PENDING_REREAD_DELAYS_MS = [2_000, 4_000, 8_000, 15_000, 30_000, 60_000];
const IN_FLIGHT = new Set(['queued', 'running']);

export interface AccountUsageSubject {
  profile_id: string;
  account_usage?: ProviderAccountUsage | null | undefined;
}

type RereadProfiles = () => Promise<readonly AccountUsageSubject[] | undefined>;

interface StoredView extends AccountUsageRefreshView {
  generation: number;
  /** The observation revision the view's notices describe. */
  revision: number;
}

type ViewUpdate = Omit<StoredView, 'revision'>;

interface PendingAttempt {
  generation: number;
  sinceRevision: number;
}

/** Whether a visible page should ask the backend to collect this profile again. */
export function needsVisibilityRefresh(
  usage: ProviderAccountUsage | null | undefined,
  nowMs: number,
): boolean {
  if (!usage) return false;
  if (['not_supported', 'reconnect_required', 'usage_access_missing'].includes(usage.status)) {
    return false;
  }
  const refresh = usage.refresh;
  if (IN_FLIGHT.has(refresh.state)) return false;
  const nextAllowed = parseInstant(refresh.next_allowed_at);
  if (nextAllowed !== null && nextAllowed > nowMs) return false;
  // A deferral ends at a server deadline the projection does not carry; the
  // backend answers deferred_busy until then.
  if (refresh.state === 'deferred') return true;
  const lastSuccess = parseInstant(refresh.last_success_at);
  if (lastSuccess === null || nowMs - lastSuccess >= STALE_AFTER_MS) return true;
  return (usage.usage.windows ?? []).some((window) => {
    const reset = parseInstant(window.reset_at);
    const observed = parseInstant(window.observed_at);
    return reset !== null && reset <= nowMs && (observed === null || observed < reset);
  });
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

/**
 * The one refresh owner for a Settings page: a single visibility-aware timer,
 * one batched command for the displayed profiles, and bounded cache re-reads
 * while an accepted attempt is pending. Results apply only to the credential
 * generation they were requested for.
 */
class AccountUsageRefreshCoordinator {
  private profiles: readonly AccountUsageSubject[] = [];
  private reread: RereadProfiles = async () => undefined;
  private readonly views = new Map<string, StoredView>();
  private readonly pending = new Map<string, PendingAttempt>();
  private readonly lastVisibilityRequest = new Map<string, number>();
  private readonly manualInFlight = new Set<string>();
  private readonly controllers = new Set<AbortController>();
  private checkTimer: ReturnType<typeof setTimeout> | null = null;
  private rereadTimer: ReturnType<typeof setTimeout> | null = null;
  private rereadIndex = 0;
  private visibilityInFlight = false;
  private running = false;
  /** Advances on stop so work started by an earlier mount never applies. */
  private epoch = 0;

  constructor(private readonly publish: (views: Record<string, StoredView>) => void) {}

  update(profiles: readonly AccountUsageSubject[], reread: RereadProfiles): void {
    this.profiles = profiles;
    this.reread = reread;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    document.addEventListener('visibilitychange', this.onVisibilityChange);
    window.addEventListener('focus', this.check);
    this.check();
  }

  stop(): void {
    this.running = false;
    this.epoch += 1;
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    window.removeEventListener('focus', this.check);
    this.clearTimers();
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
    this.pending.clear();
    this.manualInFlight.clear();
    this.visibilityInFlight = false;
    this.views.clear();
    this.publish({});
  }

  /** Request collection for displayed profiles that are stale, in one batch. */
  readonly check = (): void => {
    if (!this.running || document.visibilityState === 'hidden') return;
    this.scheduleCheck();
    const now = Date.now();
    let tracked = false;
    for (const profile of this.profiles) {
      // Attempts started elsewhere (another tab, OAuth) settle promptly too.
      const usage = profile.account_usage;
      if (usage && IN_FLIGHT.has(usage.refresh.state) && !this.pending.has(profile.profile_id)) {
        this.pending.set(profile.profile_id, {
          generation: usage.credential_generation,
          sinceRevision: usage.snapshot_revision,
        });
        tracked = true;
      }
    }
    if (tracked) this.startReread();
    if (this.visibilityInFlight) return;

    const due = this.profiles.filter((profile) => {
      const usage = profile.account_usage;
      if (!usage || this.pending.has(profile.profile_id) || this.manualInFlight.has(profile.profile_id)) {
        return false;
      }
      const last = this.lastVisibilityRequest.get(`${profile.profile_id}:${usage.credential_generation}`);
      if (last !== undefined && now - last < VISIBILITY_REQUEST_GAP_MS) return false;
      return needsVisibilityRefresh(usage, now);
    });
    if (due.length === 0) return;
    for (const profile of due) {
      this.lastVisibilityRequest.set(
        `${profile.profile_id}:${profile.account_usage!.credential_generation}`,
        now,
      );
    }

    const epoch = this.epoch;
    const ids = due.map((profile) => profile.profile_id);
    const batches: string[][] = [];
    for (let index = 0; index < ids.length; index += MAX_PROFILES_PER_REQUEST) {
      batches.push(ids.slice(index, index + MAX_PROFILES_PER_REQUEST));
    }
    this.visibilityInFlight = true;
    void Promise.allSettled(batches.map((batch) => this.send(batch, 'visibility'))).then((settled) => {
      if (epoch !== this.epoch) return;
      this.visibilityInFlight = false;
      for (const outcome of settled) {
        if (outcome.status === 'fulfilled') this.apply(outcome.value);
      }
    });
  };

  readonly requestManualRefresh = (profileId: string): void => {
    const usage = this.profiles.find((profile) => profile.profile_id === profileId)?.account_usage;
    if (!this.running || !usage || this.manualInFlight.has(profileId)) return;
    const generation = usage.credential_generation;
    const epoch = this.epoch;
    this.manualInFlight.add(profileId);
    this.setView(profileId, { generation, checking: true });
    void this.send([profileId], 'manual').then(
      (results) => {
        if (epoch !== this.epoch) return;
        this.manualInFlight.delete(profileId);
        this.apply(results);
        const view = this.views.get(profileId);
        if (view?.generation === generation && view.checking && !this.pending.has(profileId)) {
          this.setView(profileId, { ...view, checking: false });
        }
      },
      (error: unknown) => {
        if (epoch !== this.epoch || isAbortError(error)) return;
        this.manualInFlight.delete(profileId);
        if (this.views.get(profileId)?.generation === generation) {
          this.setView(profileId, { generation, checking: false, requestFailed: true });
        }
      },
    );
  };

  private readonly onVisibilityChange = (): void => {
    if (document.visibilityState === 'hidden') {
      this.pause();
    } else {
      this.check();
    }
  };

  private pause(): void {
    this.clearTimers();
    for (const [profileId, attempt] of this.pending) {
      this.setView(profileId, { generation: attempt.generation, checking: false });
    }
    this.pending.clear();
  }

  private clearTimers(): void {
    if (this.checkTimer !== null) clearTimeout(this.checkTimer);
    if (this.rereadTimer !== null) clearTimeout(this.rereadTimer);
    this.checkTimer = null;
    this.rereadTimer = null;
  }

  private scheduleCheck(): void {
    if (this.checkTimer !== null) clearTimeout(this.checkTimer);
    this.checkTimer = setTimeout(this.check, VISIBLE_CHECK_INTERVAL_MS);
  }

  private async send(profileIds: string[], trigger: RefreshRequest['trigger']): Promise<RefreshResult[]> {
    const controller = new AbortController();
    this.controllers.add(controller);
    try {
      const body: RefreshRequest = { profile_ids: profileIds, trigger };
      const response = await fetch(ACCOUNT_USAGE_REFRESH_PATH, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) throw await responseError(response, 'Failed to request account usage refresh');
      return ((await response.json()) as RefreshResponse).results;
    } finally {
      this.controllers.delete(controller);
    }
  }

  private apply(results: readonly RefreshResult[]): void {
    const now = Date.now();
    let accepted = false;
    let outdated = false;
    for (const result of results) {
      const usage = this.profiles.find((profile) => profile.profile_id === result.profile_id)?.account_usage;
      // An answer for a replaced account or a removed profile is obsolete.
      if (!usage || usage.credential_generation !== result.credential_generation) continue;
      const generation = usage.credential_generation;
      // The server holds a newer collection or refresh state than the cached list.
      if (result.outcome === 'already_fresh' || result.refresh_state !== usage.refresh.state) outdated = true;
      if (result.outcome === 'accepted') {
        if (document.visibilityState === 'hidden') {
          this.setView(result.profile_id, { generation, checking: false });
          continue;
        }
        this.pending.set(result.profile_id, { generation, sinceRevision: usage.snapshot_revision });
        this.setView(result.profile_id, { generation, checking: true });
        accepted = true;
        continue;
      }
      const retryAtMs =
        parseInstant(result.next_allowed_at) ??
        (result.retry_after_seconds != null ? now + result.retry_after_seconds * 1000 : null);
      this.setView(result.profile_id, {
        generation,
        checking: this.pending.has(result.profile_id),
        outcome: result.outcome,
        retryAtMs: result.outcome === 'backoff' || result.outcome === 'deferred_busy' ? retryAtMs : null,
      });
    }
    // Pending re-reads reload the whole list, so one is enough either way.
    if (accepted) this.startReread();
    else if (outdated) this.rereadOnce();
  }

  private rereadOnce(): void {
    if (!this.running || document.visibilityState === 'hidden') return;
    // A failed re-read changes nothing; the next check asks again.
    void this.reread().catch(() => undefined);
  }

  private startReread(): void {
    this.rereadIndex = 0;
    this.scheduleReread();
  }

  private scheduleReread(): void {
    if (this.rereadTimer !== null) clearTimeout(this.rereadTimer);
    this.rereadTimer = null;
    if (!this.running || this.pending.size === 0 || document.visibilityState === 'hidden') return;
    const delay = PENDING_REREAD_DELAYS_MS[this.rereadIndex];
    if (delay === undefined) {
      for (const [profileId, attempt] of this.pending) {
        this.setView(profileId, { generation: attempt.generation, checking: false, timedOut: true });
      }
      this.pending.clear();
      return;
    }
    const epoch = this.epoch;
    this.rereadTimer = setTimeout(() => {
      this.rereadTimer = null;
      this.rereadIndex += 1;
      void this.reread().then(
        (latest) => {
          if (epoch !== this.epoch) return;
          this.settle(latest ?? this.profiles);
          this.scheduleReread();
        },
        // A failed re-read proves nothing about the attempt; keep waiting.
        () => {
          if (epoch === this.epoch) this.scheduleReread();
        },
      );
    }, delay);
  }

  /** Stop waiting for attempts whose cached state is terminal, deferred or newer. */
  private settle(latest: readonly AccountUsageSubject[]): void {
    for (const [profileId, attempt] of this.pending) {
      const usage = latest.find((profile) => profile.profile_id === profileId)?.account_usage;
      if (usage && usage.credential_generation === attempt.generation) {
        const done = usage.snapshot_revision > attempt.sinceRevision || !IN_FLIGHT.has(usage.refresh.state);
        if (!done) continue;
      }
      this.pending.delete(profileId);
      this.setView(profileId, { generation: attempt.generation, checking: false });
    }
  }

  private setView(profileId: string, view: ViewUpdate): void {
    const usage = this.profiles.find((profile) => profile.profile_id === profileId)?.account_usage;
    this.views.set(profileId, { ...view, revision: usage?.snapshot_revision ?? 0 });
    this.publish(Object.fromEntries(this.views));
  }
}

export interface ProviderAccountUsageRefresh {
  /** Client refresh state for the profile's current credential generation. */
  viewFor: (profile: AccountUsageSubject) => AccountUsageRefreshView | undefined;
  requestManualRefresh: (profileId: string) => void;
}

export function useProviderAccountUsageRefresh(
  profiles: readonly AccountUsageSubject[],
  rereadProfiles: RereadProfiles,
): ProviderAccountUsageRefresh {
  const [views, setViews] = useState<Record<string, StoredView>>({});
  const [coordinator] = useState(() => new AccountUsageRefreshCoordinator(setViews));
  const subjectKey = profiles
    .map((profile) =>
      profile.account_usage ? `${profile.profile_id}:${profile.account_usage.credential_generation}` : '',
    )
    .join('|');

  useEffect(() => {
    coordinator.update(profiles, rereadProfiles);
  });
  useEffect(() => {
    coordinator.start();
    return () => coordinator.stop();
  }, [coordinator]);
  useEffect(() => {
    coordinator.check();
  }, [coordinator, subjectKey]);

  return {
    viewFor: (profile) => {
      const usage = profile.account_usage;
      const view = views[profile.profile_id];
      if (!usage || !view || view.generation !== usage.credential_generation) return undefined;
      // A newer observation supersedes outcome notices; retry timing still holds.
      return view.revision === usage.snapshot_revision
        ? view
        : { checking: view.checking, retryAtMs: view.retryAtMs };
    },
    requestManualRefresh: coordinator.requestManualRefresh,
  };
}
