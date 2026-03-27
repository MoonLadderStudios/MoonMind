// shared API client layer
export async function fetchApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${path.startsWith('/') ? '' : '/'}${path}`;
  
  const headers: HeadersInit = {
    ...(options.headers as HeadersInit ?? {}),
  };

  const hasContentTypeHeader = Object.keys(headers as Record<string, string>).some(
    (key) => key.toLowerCase() === 'content-type',
  );

  const body = options.body as BodyInit | null | undefined;
  if (!hasContentTypeHeader && body && !(body instanceof FormData)) {
    (headers as Record<string, string>)['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(`API Error: ${response.status} ${response.statusText} - ${errorBody}`);
  }

  // Some endpoints might return empty body on success (like DELETE)
  if (response.status === 204) {
    return {} as T;
  }

  const contentType = response.headers.get('content-type');
  if (contentType && contentType.includes('application/json')) {
    return response.json();
  }
  
  return {} as T;
}
