import { describe, it, expect } from 'vitest';

import { ApiError, getErrorStatus } from '../lib/api/client';
import {
  CONFIG_STALE_TIME_MS,
  MAX_QUERY_RETRIES,
  configQueryDefaults,
  createDashboardQueryClient,
  isTransientError,
  shouldRetryQuery,
} from './queryClient';

describe('getErrorStatus', () => {
  it('reads the status from an ApiError', () => {
    expect(getErrorStatus(new ApiError(403, 'Forbidden', 'nope'))).toBe(403);
  });

  it('parses the status from a generic API Error message', () => {
    expect(getErrorStatus(new Error('API Error: 500 Server Error - boom'))).toBe(500);
  });

  it('returns null when no status is available', () => {
    expect(getErrorStatus(new Error('network down'))).toBeNull();
    expect(getErrorStatus('not an error')).toBeNull();
    expect(getErrorStatus(undefined)).toBeNull();
  });
});

describe('isTransientError', () => {
  it('treats network/unknown errors (no status) as transient', () => {
    expect(isTransientError(new Error('Failed to fetch'))).toBe(true);
  });

  it('treats 5xx responses as transient', () => {
    expect(isTransientError(new ApiError(503, 'Unavailable', ''))).toBe(true);
  });

  it('treats permission and validation errors as non-transient', () => {
    expect(isTransientError(new ApiError(401, 'Unauthorized', ''))).toBe(false);
    expect(isTransientError(new ApiError(403, 'Forbidden', ''))).toBe(false);
    expect(isTransientError(new ApiError(400, 'Bad Request', ''))).toBe(false);
    expect(isTransientError(new ApiError(422, 'Unprocessable', ''))).toBe(false);
  });
});

describe('shouldRetryQuery', () => {
  it('retries transient failures up to the shared maximum', () => {
    const transient = new ApiError(500, 'Server Error', '');
    expect(shouldRetryQuery(0, transient)).toBe(true);
    expect(shouldRetryQuery(MAX_QUERY_RETRIES - 1, transient)).toBe(true);
    expect(shouldRetryQuery(MAX_QUERY_RETRIES, transient)).toBe(false);
  });

  it('never retries permission/validation failures', () => {
    expect(shouldRetryQuery(0, new ApiError(403, 'Forbidden', ''))).toBe(false);
    expect(shouldRetryQuery(0, new ApiError(422, 'Unprocessable', ''))).toBe(false);
  });
});

describe('createDashboardQueryClient', () => {
  it('applies only the retry policy client-wide so live queries keep their freshness', () => {
    const client = createDashboardQueryClient();
    const defaults = client.getDefaultOptions();

    expect(defaults.queries?.retry).toBe(shouldRetryQuery);
    expect(defaults.mutations?.retry).toBe(false);
    // Stale-time / focus-refetch tuning must NOT be a client-wide default, or it
    // would silently change the freshness of live queries.
    expect(defaults.queries?.staleTime).toBeUndefined();
    expect(defaults.queries?.refetchOnWindowFocus).toBeUndefined();
  });
});

describe('configQueryDefaults', () => {
  it('carries the opt-in config/catalog freshness policy', () => {
    expect(configQueryDefaults.staleTime).toBe(CONFIG_STALE_TIME_MS);
    expect(configQueryDefaults.refetchOnWindowFocus).toBe(false);
  });
});
