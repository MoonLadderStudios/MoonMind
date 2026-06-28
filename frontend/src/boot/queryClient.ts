import type { QueryObserverOptions } from '@tanstack/react-query';
import { QueryClient } from '@tanstack/react-query';

import { getErrorStatus } from '../lib/api/client';

/**
 * Shared React Query defaults for the dashboard shell (MM-960).
 *
 * Goals:
 * - Retry only transient failures (network errors and 5xx responses).
 * - Never retry permission (401/403) or validation (400/422 and other 4xx) errors.
 *
 * The retry policy is the only client-wide default: it is safe for every query.
 * Stale-time / focus-refetch tuning is opt-in per query via {@link configQueryDefaults}
 * so it applies only to slowly-changing config/catalog data and never silently
 * changes the freshness of live queries (e.g. the schedules list), which keep
 * React Query's standard stale-on-mount and refetch-on-focus behavior.
 */

/** Maximum number of retries for transient query failures. */
export const MAX_QUERY_RETRIES = 3;

/** Standard stale time for config/catalog data (5 minutes). */
export const CONFIG_STALE_TIME_MS = 5 * 60 * 1000;

/**
 * A 4xx status indicates a client error (permission or validation) that will
 * not succeed on retry. 5xx and network-level failures (no status) are treated
 * as transient.
 */
export function isTransientError(error: unknown): boolean {
  const status = getErrorStatus(error);
  if (status === null) {
    // No HTTP status (network error, timeout, unexpected throw) — treat as transient.
    return true;
  }
  return status >= 500 && status <= 599;
}

/** React Query `retry` predicate implementing the shared default policy. */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (!isTransientError(error)) {
    return false;
  }
  return failureCount < MAX_QUERY_RETRIES;
}

/**
 * Per-query options for config/catalog style data. Spread into a `useQuery`
 * call (`useQuery({ ...configQueryDefaults, queryKey, queryFn })`) to apply the
 * 5-minute stale window and skip focus refetches for slowly-changing data.
 * Live queries should omit this so they keep refetching as expected.
 */
export const configQueryDefaults = {
  staleTime: CONFIG_STALE_TIME_MS,
  refetchOnWindowFocus: false,
} satisfies Pick<QueryObserverOptions, 'staleTime' | 'refetchOnWindowFocus'>;

/** Build a QueryClient pre-configured with the shared dashboard defaults. */
export function createDashboardQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetryQuery,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
