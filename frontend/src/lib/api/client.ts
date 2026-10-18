// shared API client layer

/**
 * Error thrown for non-2xx API responses. It keeps the HTTP status so the
 * shared retry policy can tell transient failures from permanent ones, and the
 * parsed FastAPI `detail` so callers can render structured errors.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly body: string;
  /** Parsed `detail` from a JSON error body, or null when there was none. */
  readonly detail: unknown;
  /** Machine-readable `detail.code`, when the API supplied one. */
  readonly code: string | null;

  constructor(
    status: number,
    statusText: string,
    body: string,
    options: { message?: string; detail?: unknown; code?: string | null } = {},
  ) {
    super(options.message ?? readableErrorMessage(status, statusText, options.detail, body));
    this.name = 'ApiError';
    this.status = status;
    this.statusText = statusText;
    this.body = body;
    this.detail = options.detail ?? null;
    this.code = options.code ?? null;
  }
}

const MAX_TEXT_ERROR_LENGTH = 300;

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function detailMessage(detail: unknown): string | null {
  if (typeof detail === 'string') return detail.trim() || null;
  if (Array.isArray(detail)) {
    for (const entry of detail) {
      const message = detailMessage(entry);
      if (message) return message;
    }
    return null;
  }
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>;
    for (const key of ['message', 'msg', 'detail', 'error']) {
      const message = detailMessage(record[key]);
      if (message) return message;
    }
  }
  return null;
}

function readableErrorMessage(
  status: number,
  statusText: string,
  detail: unknown,
  body: string,
): string {
  const fromDetail = detailMessage(detail);
  if (fromDetail) return fromDetail;
  const text = body.trim();
  // Short plain-text bodies are usually a deliberate message; markup is an
  // error page from a proxy and only adds noise.
  if (text && !text.startsWith('<') && text.length <= MAX_TEXT_ERROR_LENGTH && detail == null) {
    return text;
  }
  return statusText.trim() || `Request failed with HTTP ${status}`;
}

/**
 * Build a typed {@link ApiError} from a failed response. `context` prefixes the
 * readable message ("Failed to fetch workflow: Not Found"). Use it in place of
 * `new Error(response.statusText)` so the status survives for retry decisions
 * and not-found/unavailable states.
 */
async function readBody(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return '';
  }
}

export async function responseError(response: Response, context?: string): Promise<ApiError> {
  const body = await readBody(response);
  const parsed = body ? parseJson(body) : undefined;
  const detail =
    parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? ((parsed as Record<string, unknown>).detail ?? parsed)
      : null;
  const codeValue =
    detail && typeof detail === 'object' && !Array.isArray(detail)
      ? (detail as Record<string, unknown>).code
      : null;
  const message = readableErrorMessage(response.status, response.statusText, detail, body);
  return new ApiError(response.status, response.statusText, body, {
    message: context ? `${context}: ${message}` : message,
    detail,
    code: typeof codeValue === 'string' ? codeValue : null,
  });
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

export type ApiErrorKind =
  | 'not_found'
  | 'unauthorized'
  | 'forbidden'
  | 'client'
  | 'unavailable'
  | 'network';

/** Classify a failure for choosing between not-found, access and unavailable states. */
export function apiErrorKind(error: unknown): ApiErrorKind {
  const status = getErrorStatus(error);
  if (status === null) return 'network';
  if (status === 404 || status === 410) return 'not_found';
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status >= 400 && status < 500) return 'client';
  return 'unavailable';
}

function withDefaultHeaders(options: RequestInit): Headers {
  const headers = new Headers(options.headers);
  if (typeof options.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return headers;
}

/**
 * Fetch an API path and return the unread response after checking its status.
 * Streaming and download callers read the body themselves.
 */
export async function fetchApiResponse(path: string, options: RequestInit = {}): Promise<Response> {
  const url = `${path.startsWith('/') ? '' : '/'}${path}`;
  const response = await fetch(url, { ...options, headers: withDefaultHeaders(options) });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response;
}

/**
 * Fetch an API path and decode its body: JSON when the response says so, text
 * otherwise, and `undefined` when the body is empty (204, or an empty 200).
 */
export async function fetchApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetchApiResponse(path, options);
  if (response.status === 204 || response.status === 205) {
    return undefined as T;
  }
  const text = await response.text();
  if (!text.trim()) {
    return undefined as T;
  }
  const contentType = response.headers.get('Content-Type') || '';
  if (/[/+]json\b/i.test(contentType)) {
    return JSON.parse(text) as T;
  }
  return text as T;
}
