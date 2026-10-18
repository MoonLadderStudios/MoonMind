import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError,
  apiErrorKind,
  fetchApi,
  fetchApiResponse,
  responseError,
} from './client';

function stubFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('responseError', () => {
  it('exposes the status and the FastAPI string detail as a readable message', async () => {
    const error = await responseError(
      new Response(JSON.stringify({ detail: 'Workflow not found' }), {
        status: 404,
        statusText: 'Not Found',
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.detail).toBe('Workflow not found');
    expect(error.message).toBe('Workflow not found');
  });

  it('reads a structured detail message and code, prefixed with the caller context', async () => {
    const error = await responseError(
      new Response(
        JSON.stringify({ detail: { code: 'invalid_execution_query', message: 'bad state' } }),
        { status: 422, statusText: 'Unprocessable Entity', headers: { 'Content-Type': 'application/json' } },
      ),
      'Failed to fetch workflows',
    );

    expect(error.code).toBe('invalid_execution_query');
    expect(error.detail).toEqual({ code: 'invalid_execution_query', message: 'bad state' });
    expect(error.message).toBe('Failed to fetch workflows: bad state');
  });

  it('summarizes validation-error arrays', async () => {
    const error = await responseError(
      new Response(JSON.stringify({ detail: [{ loc: ['body', 'title'], msg: 'Field required' }] }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    expect(error.message).toBe('Field required');
  });

  it('falls back to the status text instead of echoing an HTML error page', async () => {
    const error = await responseError(
      new Response('<html><body><h1>502 Bad Gateway</h1></body></html>', {
        status: 502,
        statusText: 'Bad Gateway',
        headers: { 'Content-Type': 'text/html' },
      }),
      'Failed to fetch workflow',
    );

    expect(error.message).toBe('Failed to fetch workflow: Bad Gateway');
    expect(error.body).toContain('502 Bad Gateway');
  });

  it('describes an empty error response by its status code', async () => {
    const error = await responseError(new Response(null, { status: 503 }));

    expect(error.message).toBe('Request failed with HTTP 503');
  });
});

describe('apiErrorKind', () => {
  it('classifies errors by HTTP status', () => {
    expect(apiErrorKind(new ApiError(404, 'Not Found', ''))).toBe('not_found');
    expect(apiErrorKind(new ApiError(410, 'Gone', ''))).toBe('not_found');
    expect(apiErrorKind(new ApiError(401, 'Unauthorized', ''))).toBe('unauthorized');
    expect(apiErrorKind(new ApiError(403, 'Forbidden', ''))).toBe('forbidden');
    expect(apiErrorKind(new ApiError(422, 'Unprocessable', ''))).toBe('client');
    expect(apiErrorKind(new ApiError(503, 'Unavailable', ''))).toBe('unavailable');
    expect(apiErrorKind(new TypeError('Failed to fetch'))).toBe('network');
  });
});

describe('fetchApi', () => {
  it('returns undefined for an empty successful body', async () => {
    stubFetch(new Response('', { status: 200, headers: { 'Content-Type': 'application/json' } }));

    await expect(fetchApi('/api/thing', { method: 'DELETE' })).resolves.toBeUndefined();
  });

  it('returns text for a non-JSON successful body', async () => {
    stubFetch(new Response('plain words', { status: 200, headers: { 'Content-Type': 'text/plain' } }));

    await expect(fetchApi<string>('/api/thing')).resolves.toBe('plain words');
  });

  it('parses JSON bodies', async () => {
    stubFetch(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    }));

    await expect(fetchApi<{ ok: boolean }>('api/thing')).resolves.toEqual({ ok: true });
  });

  it('throws a typed ApiError with the structured detail', async () => {
    stubFetch(new Response(JSON.stringify({ detail: 'Not allowed' }), {
      status: 403,
      statusText: 'Forbidden',
      headers: { 'Content-Type': 'application/json' },
    }));

    const error = await fetchApi('/api/thing').catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);
    expect((error as ApiError).message).toBe('Not allowed');
  });

  it('only declares a JSON content type for string bodies', async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }));
    const form = new FormData();
    form.append('file', new Blob(['x']), 'x.txt');

    await fetchApi('/api/upload', { method: 'POST', body: form });
    await fetchApi('/api/json', { method: 'POST', body: JSON.stringify({ a: 1 }) });

    const uploadHeaders = new Headers(fetchMock.mock.calls[0]?.[1].headers);
    const jsonHeaders = new Headers(fetchMock.mock.calls[1]?.[1].headers);
    expect(uploadHeaders.has('Content-Type')).toBe(false);
    expect(jsonHeaders.get('Content-Type')).toBe('application/json');
  });
});

describe('fetchApiResponse', () => {
  it('returns the unread response so streaming callers can consume the body', async () => {
    stubFetch(new Response('chunk-1chunk-2', { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));

    const response = await fetchApiResponse('/api/stream');

    expect(response.bodyUsed).toBe(false);
    await expect(response.text()).resolves.toBe('chunk-1chunk-2');
  });

  it('rejects non-2xx responses with a typed error', async () => {
    stubFetch(new Response('', { status: 500, statusText: 'Internal Server Error' }));

    await expect(fetchApiResponse('/api/stream')).rejects.toMatchObject({ status: 500 });
  });
});
