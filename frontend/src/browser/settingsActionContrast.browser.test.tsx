import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { userEvent } from 'vitest/browser';
import { BrowserRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { ProvidersSecretsSettingsPage } from '../entrypoints/settings';
import { renderWithClient, screen, within } from '../utils/test-utils';
import { hover, keyboardFocus, outlineContrast, setTheme, textContrast, type Theme } from './support/computedColor';
import '../styles/dashboard.css';

// Real-browser contrast check for the Settings provider-row and secret-row
// actions. These used to be Tailwind-only buttons with no background, so they
// sat on the bare button's purple fill (about 1.1:1 in light theme).

const payload: BootPayload = {
  page: 'settings-providers-secrets',
  apiBase: '/api',
  initialData: {
    settingsPermissions: [
      'provider_profiles.read',
      'provider_profiles.write',
      'secrets.metadata.read',
      'secrets.write',
    ],
  },
} as unknown as BootPayload;

const baseProfile = {
  runtime_id: 'codex_cli',
  provider_id: 'openai',
  max_parallel_runs: 1,
  cooldown_after_429_seconds: 300,
  rate_limit_policy: 'backoff',
  enabled: true,
  is_default: false,
  auth_state: 'connected',
  disabled_reason: null,
};

const profiles = [
  {
    ...baseProfile,
    profile_id: 'codex-oauth',
    credential_source: 'oauth_volume',
    runtime_materialization_mode: 'oauth_home',
    secret_refs: {},
    volume_ref: 'codex_auth_volume',
    volume_mount_path: '/home/app/.codex',
    account_label: 'Codex account',
    is_default: true,
  },
  {
    ...baseProfile,
    profile_id: 'claude-anthropic-connected',
    runtime_id: 'claude_code',
    provider_id: 'anthropic',
    credential_source: 'oauth_volume',
    runtime_materialization_mode: 'oauth_home',
    secret_refs: {},
    volume_ref: 'claude_auth_volume',
    volume_mount_path: '/home/app/.claude',
    account_label: 'Claude Anthropic OAuth',
    command_behavior: {
      auth_strategy: 'claude_credential_methods',
      auth_state: 'connected',
      auth_actions: ['connect_oauth', 'use_api_key', 'validate_oauth', 'disconnect_oauth'],
      auth_status_label: 'Claude OAuth ready',
    },
  },
];

const secrets = {
  items: [
    {
      slug: 'github-pat-main',
      secretRef: 'db://github-pat-main',
      status: 'active',
      details: {},
      createdAt: '2026-04-28T00:00:00Z',
      updatedAt: '2026-04-28T00:00:00Z',
    },
  ],
};

let shell: HTMLElement;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

async function renderSettings(): Promise<{ providerActions: HTMLButtonElement[]; secretActions: HTMLButtonElement[] }> {
  renderWithClient(
    <BrowserRouter>
      <ProvidersSecretsSettingsPage payload={payload} />
    </BrowserRouter>,
    { container: shell },
  );
  await screen.findByRole('button', { name: 'Disconnect OAuth claude-anthropic-connected' });
  const secretCell = (await screen.findByText('github-pat-main')).closest('tr');
  if (!secretCell) throw new Error('secret row did not render');
  const providerActions = Array.from(
    document.querySelectorAll<HTMLElement>('td[data-label="Actions"]'),
  ).flatMap((cell) => within(cell).getAllByRole('button') as HTMLButtonElement[]);
  const secretActions = within(secretCell).getAllByRole('button') as HTMLButtonElement[];
  return { providerActions, secretActions };
}

function nameOf(element: HTMLElement): string {
  return element.getAttribute('aria-label') ?? element.textContent ?? element.outerHTML.slice(0, 80);
}

beforeEach(() => {
  window.history.pushState({}, 'Settings', '/settings/providers-secrets');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles')) return jsonResponse(profiles);
      if (url.startsWith('/api/v1/secrets')) return jsonResponse(secrets);
      return jsonResponse({}, 404);
    }),
  );
  shell = document.createElement('main');
  shell.className = 'dashboard-root';
  document.body.appendChild(shell);
});

afterEach(() => {
  shell.remove();
  vi.unstubAllGlobals();
  setTheme('light');
});

for (const theme of ['light', 'dark'] as Theme[]) {
  describe(`Settings row actions (${theme} theme)`, () => {
    beforeEach(() => setTheme(theme));

    it('keep at least 4.5:1 text contrast at rest and on hover', async () => {
      const { providerActions, secretActions } = await renderSettings();
      expect(providerActions.length).toBeGreaterThanOrEqual(8);
      expect(secretActions.length).toBeGreaterThanOrEqual(4);
      for (const action of [...providerActions, ...secretActions]) {
        expect(action.classList.contains('button'), nameOf(action)).toBe(true);
        if (action.disabled) continue;
        expect(textContrast(action), `${nameOf(action)} at rest`).toBeGreaterThanOrEqual(4.5);
        await hover(action);
        expect(textContrast(action), `${nameOf(action)} hovered`).toBeGreaterThanOrEqual(4.5);
      }
      await userEvent.unhover(shell);
    });

    it('show a 3:1 focus indicator that stays visible while hovered', async () => {
      const { providerActions, secretActions } = await renderSettings();
      for (const action of [providerActions[0]!, secretActions[0]!]) {
        await keyboardFocus(action);
        expect(outlineContrast(action), `${nameOf(action)} focus`).toBeGreaterThanOrEqual(3);
        await hover(action);
        expect(outlineContrast(action), `${nameOf(action)} focus while hovered`).toBeGreaterThanOrEqual(3);
        await userEvent.unhover(action);
        action.blur();
      }
    });
  });
}
