import { QueryClient } from '@tanstack/react-query';

import { getErrorStatus } from '../lib/api/client';

/**
 * Shared React Query defaults for the dashboard shell (MM-960).
 *
 * Goals:
 * - Retry only transient failures (network errors and 5xx responses).
 * - Never retry permission (401/403) or validation (400/422 and other 4xx) errors.
 * - Apply standard stale times for config/catalog style data so the shell does
 *   not refetch slowly-changing data on every mount.
 * - Use one consistent refetch-on-window-focus policy across pages.
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

/** Build a QueryClient pre-configured with the shared dashboard defaults. */
export function createDashboardQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetryQuery,
        staleTime: CONFIG_STALE_TIME_MS,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
