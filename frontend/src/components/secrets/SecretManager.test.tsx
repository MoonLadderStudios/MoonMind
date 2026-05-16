import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SecretManager } from './SecretManager';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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

  it('shows usage references and consumer names without exposing plaintext', () => {
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false } },
          })
        }
      >
        <SecretManager
          secrets={[
            {
              slug: 'github-pat-main',
              secretRef: 'db://github-pat-main',
              status: 'active',
              details: {},
              createdAt: '2026-04-28T00:00:00Z',
              updatedAt: '2026-04-28T00:00:00Z',
              usages: [
                {
                  consumerType: 'setting_override',
                  objectName: 'Workspace setting integrations.github.token_ref',
                  reference: 'db://github-pat-main',
                  scope: 'workspace',
                  settingKey: 'integrations.github.token_ref',
                },
              ],
            },
          ]}
          onNotice={vi.fn()}
          queryClient={
            new QueryClient({
              defaultOptions: { queries: { retry: false } },
            })
          }
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText('Workspace setting integrations.github.token_ref')).toBeTruthy();
    expect(screen.getAllByText('db://github-pat-main').length).toBeGreaterThan(0);
    expect(screen.queryByText('ghp_usage_plaintext')).toBeNull();
  });

  it('loads secret usage on demand from the usage endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        secretRef: 'db://github-pat-main',
        usages: [
          {
            consumerType: 'setting_override',
            objectName: 'Workspace setting integrations.github.token_ref',
            reference: 'db://github-pat-main',
            scope: 'workspace',
            settingKey: 'integrations.github.token_ref',
          },
        ],
        diagnostics: [],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    renderSecretManager();

    fireEvent.click(screen.getByRole('button', { name: 'View usage' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/v1/secrets/github-pat-main/usage');
    });
    expect(await screen.findByText('Workspace setting integrations.github.token_ref')).toBeTruthy();
    expect(screen.queryByText('ghp_usage_plaintext')).toBeNull();
  });
});
