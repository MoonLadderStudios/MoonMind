// shared API client layer

/** Error thrown for non-2xx API responses, carrying the HTTP status. */
export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly body: string;

  constructor(status: number, statusText: string, body: string) {
    super(`API Error: ${status} ${statusText} - ${body}`);
    this.name = 'ApiError';
    this.status = status;
    this.statusText = statusText;
    this.body = body;
  }
}

/**
 * Extract the HTTP status from an error if one is available.
 *
 * Returns the numeric status for {@link ApiError} (or any error carrying a
 * numeric `status`), falls back to parsing a leading `API Error: <status>`
 * message, and returns `null` when no status can be determined (e.g. network
 * failures or unexpected throws).
 */
export function getErrorStatus(error: unknown): number | null {
  if (error && typeof error === 'object') {
    const status = (error as { status?: unknown }).status;
    if (typeof status === 'number' && Number.isFinite(status)) {
      return status;
    }
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string') {
      const match = message.match(/API Error:\s*(\d{3})\b/);
      if (match) {
        return Number(match[1]);
      }
    }
  }
  return null;
}

export async function fetchApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${path.startsWith('/') ? '' : '/'}${path}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });

  if (!response.ok) {
    const errorBody = await response.text();
    throw new ApiError(response.status, response.statusText, errorBody);
  }

  // Some endpoints might return empty body on success (like DELETE)
  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}
