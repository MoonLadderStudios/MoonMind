import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SecretManager } from './SecretManager';

afterEach(() => {
  vi.restoreAllMocks();
});

function renderSecretManager() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onNotice = vi.fn();

  render(
    <QueryClientProvider client={queryClient}>
      <SecretManager
        secrets={[
          {
            slug: 'github-pat-main',
            secretRef: 'db://github-pat-main',
            status: 'active',
            details: {},
            createdAt: '2026-04-28T00:00:00Z',
            updatedAt: '2026-04-28T00:00:00Z',
          },
        ]}
        onNotice={onNotice}
        queryClient={queryClient}
      />
    </QueryClientProvider>,
  );

  return { onNotice };
}

describe('SecretManager', () => {
  it('shows and copies managed SecretRefs without exposing plaintext', async () => {
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) };
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: clipboard,
    });
    const { onNotice } = renderSecretManager();

    expect(screen.getByText('db://github-pat-main')).toBeTruthy();
    expect(screen.queryByText(/sk-test-secret/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Copy SecretRef for github-pat-main' }));

    await waitFor(() => {
      expect(clipboard.writeText).toHaveBeenCalledWith('db://github-pat-main');
    });
    expect(onNotice).toHaveBeenCalledWith({
      level: 'ok',
      text: 'SecretRef copied.',
    });
  });
});
