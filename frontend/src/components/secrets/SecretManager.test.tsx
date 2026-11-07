import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { SecretManager } from './SecretManager';
import { SettingsDraftGuardProvider } from '../settings/SettingsDraftGuard';

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

  it('shows a Re-enable action and broken-reference indicator for disabled secrets when the user can re-enable', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        slug: 'github-pat-main',
        secretRef: 'db://github-pat-main',
        status: 'active',
        details: {},
        createdAt: '2026-04-28T00:00:00Z',
        updatedAt: '2026-04-28T00:00:00Z',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

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
              status: 'disabled',
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
          onNotice={onNotice}
          queryClient={queryClient}

        />
      </QueryClientProvider>,
    );

    // Broken-reference indicator appears next to the dependent setting
    expect(screen.getAllByText(/broken reference/i).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: 'Re-enable github-pat-main' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/secrets/github-pat-main/status',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ status: 'active' }),
        }),
      );
    });
    expect(onNotice).toHaveBeenCalledWith(
      expect.objectContaining({ level: 'ok' }),
    );
  });

  it('offers Re-enable to the admitted instance operator', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <SecretManager
          secrets={[
            {
              slug: 'github-pat-main',
              secretRef: 'db://github-pat-main',
              status: 'disabled',
              details: {},
              createdAt: '2026-04-28T00:00:00Z',
              updatedAt: '2026-04-28T00:00:00Z',
            },
          ]}
          onNotice={vi.fn()}
          queryClient={queryClient}

        />
      </QueryClientProvider>,
    );

    expect(screen.getByRole('button', { name: /re-enable github-pat-main/i })).toBeTruthy();
  });

  it('encodes slugs with reserved characters in every secret request', async () => {
    const slug = 'a/b c?#';
    const encoded = '/api/v1/secrets/a%2Fb%20c%3F%23';
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ secretRef: `db://${slug}`, usages: [], diagnostics: [] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <SecretManager
          secrets={[
            {
              slug,
              status: 'disabled',
              details: {},
              createdAt: '2026-04-28T00:00:00Z',
            },
          ]}
          onNotice={vi.fn()}
          queryClient={queryClient}

        />
      </QueryClientProvider>,
    );
    const expectRequest = (url: string, method?: string) =>
      waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          url,
          ...(method ? [expect.objectContaining({ method })] : []),
        );
      });

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('Secure value'), {
      target: { value: 'edited-value' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save new value' }));
    await expectRequest(encoded, 'PUT');

    fireEvent.click(screen.getByRole('button', { name: 'Rotate' }));
    const rotateForm = screen.getByRole('button', { name: 'Rotate now' }).closest('form');
    fireEvent.change(rotateForm!.querySelector('input')!, {
      target: { value: 'rotated-value' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Rotate now' }));
    await expectRequest(`${encoded}/rotate`, 'POST');

    fireEvent.click(screen.getByRole('button', { name: `Re-enable ${slug}` }));
    await expectRequest(`${encoded}/status`, 'PUT');

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await expectRequest(encoded, 'DELETE');

    await expectRequest(`${encoded}/usage`);

    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  // MoonLadderStudios/MoonMind#4559: the stored-secrets table degrades to
  // stacked cards below 720px, so every row cell must expose the card label
  // and the row actions must own a wrapping area instead of one unwrapped row.
  it('exposes stacked-card labels and wrapping action areas for narrow viewports', () => {
    renderSecretManager();

    const row = screen.getByText('github-pat-main').closest('tr');
    expect(row).not.toBeNull();
    const cells = Array.from(row!.querySelectorAll('td'));
    expect(cells.length).toBeGreaterThan(0);
    for (const cell of cells) {
      expect(cell.getAttribute('data-label')).toBeTruthy();
    }
    const actionsCell = row!.querySelector('td[data-label="Actions"]');
    expect(actionsCell).not.toBeNull();
    const actionsArea = actionsCell!.querySelector('div');
    expect(actionsArea).not.toBeNull();
    expect(actionsArea!.className).toContain('flex-wrap');
  });

  describe('focused add, edit, and rotate flow', () => {
    function renderGuardedSecretManager(fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })) {
      vi.stubGlobal('fetch', fetchMock);
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const onNotice = vi.fn();
      render(
        <MemoryRouter>
          <QueryClientProvider client={queryClient}>
            <SettingsDraftGuardProvider>
              <SecretManager
                secrets={[
                  {
                    slug: 'github-pat-main',
                    secretRef: 'db://github-pat-main',
                    status: 'active',
                    details: {},
                    createdAt: '2026-04-28T00:00:00Z',
                  },
                ]}
                onNotice={onNotice}
                queryClient={queryClient}
              />
            </SettingsDraftGuardProvider>
          </QueryClientProvider>
        </MemoryRouter>,
      );
      return { fetchMock, onNotice };
    }

    it('shows stored secrets as the primary view with no creation form until Add secret', () => {
      renderGuardedSecretManager();
      expect(screen.getByRole('heading', { name: 'Stored secrets' })).toBeTruthy();
      expect(screen.getByText('github-pat-main')).toBeTruthy();
      expect(screen.queryByLabelText('Secret slug')).toBeNull();
      expect(screen.queryByRole('dialog')).toBeNull();

      const add = screen.getByRole('button', { name: 'Add secret' });
      add.focus();
      fireEvent.click(add);
      const dialog = screen.getByRole('dialog', { name: 'Add secret' });
      expect(dialog.getAttribute('aria-modal')).toBe('true');
      expect(document.activeElement).toBe(screen.getByLabelText('Secret slug'));
    });

    it('saves a new secret and closes the dialog', async () => {
      const { fetchMock, onNotice } = renderGuardedSecretManager();
      screen.getByRole('button', { name: 'Add secret' }).focus();
      fireEvent.click(screen.getByRole('button', { name: 'Add secret' }));
      fireEvent.change(screen.getByLabelText('Secret slug'), { target: { value: 'OPENAI_API_KEY' } });
      fireEvent.change(screen.getByLabelText('Secure value'), { target: { value: 'sk-new' } });
      fireEvent.click(screen.getByRole('button', { name: 'Encrypt and store secret' }));

      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Add secret' })).toBeNull());
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/secrets',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ slug: 'OPENAI_API_KEY', plaintext: 'sk-new', details: {} }),
        }),
      );
      expect(onNotice).toHaveBeenCalledWith({ level: 'ok', text: 'Secret saved successfully.' });
      expect(screen.getByRole('button', { name: 'Add secret' })).toBe(document.activeElement);
    });

    it('keeps the dialog and draft open on validation and server failures', async () => {
      const fetchMock = vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: 'Secret already exists.' }) });
      renderGuardedSecretManager(fetchMock);
      fireEvent.click(screen.getByRole('button', { name: 'Add secret' }));

      fireEvent.click(screen.getByRole('button', { name: 'Encrypt and store secret' }));
      expect(within(screen.getByRole('dialog', { name: 'Add secret' })).getByRole('alert').textContent).toBe(
        'Secret slug is required.',
      );
      fireEvent.change(screen.getByLabelText('Secret slug'), { target: { value: 'github-pat-main' } });
      fireEvent.click(screen.getByRole('button', { name: 'Encrypt and store secret' }));
      expect(screen.getByRole('alert').textContent).toBe('Provide a secure value for the new secret.');
      expect(fetchMock).not.toHaveBeenCalled();

      fireEvent.change(screen.getByLabelText('Secure value'), { target: { value: 'duplicate' } });
      fireEvent.click(screen.getByRole('button', { name: 'Encrypt and store secret' }));
      await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Secret already exists.'));
      expect((screen.getByLabelText('Secret slug') as HTMLInputElement).value).toBe('github-pat-main');
      expect((screen.getByLabelText('Secure value') as HTMLInputElement).value).toBe('duplicate');
    });

    it('cancels a clean form at once and protects an entered value', () => {
      const { fetchMock } = renderGuardedSecretManager();
      fireEvent.click(screen.getByRole('button', { name: 'Add secret' }));
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByRole('dialog', { name: 'Add secret' })).toBeNull();

      fireEvent.click(screen.getByRole('button', { name: 'Add secret' }));
      fireEvent.change(screen.getByLabelText('Secure value'), { target: { value: 'typed-value' } });
      fireEvent.keyDown(screen.getByRole('dialog', { name: 'Add secret' }), { key: 'Escape' });
      expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
      expect((screen.getByLabelText('Secure value') as HTMLInputElement).value).toBe('typed-value');

      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      fireEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));
      expect(screen.queryByRole('dialog', { name: 'Add secret' })).toBeNull();
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('opens edit and rotate in context for the chosen secret', async () => {
      const { fetchMock } = renderGuardedSecretManager();
      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      const edit = screen.getByRole('dialog', { name: 'Update secret value: github-pat-main' });
      expect((within(edit).getByLabelText('Secret slug') as HTMLInputElement).disabled).toBe(true);
      expect(document.activeElement).toBe(within(edit).getByLabelText('Secure value'));
      fireEvent.click(within(edit).getByRole('button', { name: 'Cancel' }));

      fireEvent.click(screen.getByRole('button', { name: 'Rotate' }));
      const rotate = screen.getByRole('dialog', { name: 'Rotate Secret: github-pat-main' });
      fireEvent.click(within(rotate).getByRole('button', { name: 'Rotate now' }));
      expect(within(rotate).getByRole('alert').textContent).toBe('Provide a new secure value to rotate the secret.');
      fireEvent.change(within(rotate).getByLabelText('New secure value'), { target: { value: 'rotated' } });
      fireEvent.click(within(rotate).getByRole('button', { name: 'Rotate now' }));
      await waitFor(() => expect(screen.queryByRole('dialog', { name: /Rotate Secret/ })).toBeNull());
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/secrets/github-pat-main/rotate',
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });
});

describe('broken secret automatic Usage', () => {
  function show(status = 'disabled') {
    const client = new QueryClient();
    return render(<QueryClientProvider client={client}><SecretManager queryClient={client} onNotice={vi.fn()}
      secrets={[{ slug: 'api-key', status, details: {}, createdAt: '2026-11-06' }]} /></QueryClientProvider>);
  }
  it.each(['disabled', 'rotated', 'missing'])('loads Usage without a click for %s secrets and anchors the row', async (status) => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      secretRef: 'db://api-key', usages: [{ consumerType: 'profile', objectName: 'bound-profile', reference: 'db://api-key' }],
    }) });
    vi.stubGlobal('fetch', fetchMock);
    show(status);
    const consumer = await screen.findByText('bound-profile');
    expect(consumer.closest('tr')?.id).toBe('managed-secret-api-key');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/secrets/api-key/usage');
  });
  it('keeps a Usage error visible and does not retry on rerender', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: 'Usage unavailable' }) });
    vi.stubGlobal('fetch', fetchMock);
    show();
    await screen.findByText('Usage unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Add secret' }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
