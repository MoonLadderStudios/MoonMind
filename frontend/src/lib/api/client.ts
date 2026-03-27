// shared API client layer
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
    throw new Error(`API Error: ${response.status} ${response.statusText} - ${errorBody}`);
  }

  // Some endpoints might return empty body on success (like DELETE)
  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}
