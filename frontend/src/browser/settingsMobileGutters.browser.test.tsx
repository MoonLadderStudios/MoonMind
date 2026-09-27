import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';
import { BrowserRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { ProvidersSecretsSettingsPage } from '../entrypoints/settings';
import { renderWithClient, screen } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser guardrail for the Providers & Secrets phone layout. The
// production page renders inside the dashboard shell, whose root already owns
// the one page gutter; every card or group nested inside it may add only one
// compact inset so phones keep their reading width.

const PHONE_VIEWPORTS = [320, 360, 390, 430] as const;
const TOLERANCE_PX = 1.5;
// One compact card inset: a 1px border plus 0.75rem padding.
const LEVEL_INSET_PX = 13;

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

const savedProfile = {
  profile_id: 'codex_team_oauth',
  runtime_id: 'codex_cli',
  provider_id: 'openai',
  credential_source: 'oauth_volume',
  runtime_materialization_mode: 'oauth_home',
  max_parallel_runs: 1,
  cooldown_after_429_seconds: 300,
  rate_limit_policy: 'backoff',
  enabled: true,
  is_default: true,
};

let shell: HTMLElement;
let panel: HTMLElement;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function inlineInsets(child: Element, ancestor: Element): { left: number; right: number } {
  const childRect = child.getBoundingClientRect();
  const ancestorRect = ancestor.getBoundingClientRect();
  return {
    left: childRect.left - ancestorRect.left,
    right: ancestorRect.right - childRect.right,
  };
}

function expectInsetAtMost(child: Element, ancestor: Element, maxPx: number, label: string): void {
  const { left, right } = inlineInsets(child, ancestor);
  expect(left, `${label}: left inset ${left.toFixed(1)}px`).toBeLessThanOrEqual(maxPx + TOLERANCE_PX);
  expect(right, `${label}: right inset ${right.toFixed(1)}px`).toBeLessThanOrEqual(maxPx + TOLERANCE_PX);
}

function rootContentInset(): number {
  return parseFloat(getComputedStyle(shell).paddingLeft);
}

async function renderProvidersPage(): Promise<void> {
  renderWithClient(
    <BrowserRouter>
      <ProvidersSecretsSettingsPage payload={payload} />
    </BrowserRouter>,
    { container: panel },
  );
  await screen.findByRole('list', { name: 'Model and effort tiers' });
  await screen.findByText('Managed Secrets');
}

beforeEach(() => {
  document.body.style.margin = '0';
  window.history.pushState({}, 'Settings', '/settings/providers-secrets');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles')) {
        return jsonResponse([savedProfile]);
      }
      if (url.startsWith('/api/v1/secrets')) {
        return jsonResponse({ items: [] });
      }
      return jsonResponse({}, 404);
    }),
  );
  shell = document.createElement('main');
  shell.className = 'dashboard-root';
  const content = document.createElement('div');
  content.className = 'dashboard-content';
  panel = document.createElement('section');
  panel.className = 'panel panel--data-wide';
  content.appendChild(panel);
  shell.appendChild(content);
  document.body.appendChild(shell);
});

afterEach(async () => {
  shell.remove();
  document.body.style.margin = '';
  vi.unstubAllGlobals();
  await page.viewport(1280, 800);
});

describe('Providers & Secrets phone gutters', () => {
  it.each(PHONE_VIEWPORTS)('uses the dashboard root as the only page gutter at %spx', async (width) => {
    await page.viewport(width, 800);
    await renderProvidersPage();
    const rootGutter = rootContentInset();
    const header = document.querySelector('.settings-page > header')!;
    const profiles = document.querySelector('.provider-profiles')!;

    // Settings cards start at the root content edge; the page frame adds no
    // second gutter on top of the dashboard root.
    expectInsetAtMost(header, shell, rootGutter, 'page header card');
    expectInsetAtMost(profiles, shell, rootGutter, 'Profiles section');
    expectInsetAtMost(header.querySelector('h2')!, header, LEVEL_INSET_PX, 'page title');

    // Sections stay visually separate without desktop-sized vertical gaps.
    const gap = profiles.getBoundingClientRect().top - header.getBoundingClientRect().bottom;
    expect(gap, 'gap between settings sections').toBeGreaterThan(0);
    const sections = Array.from(document.querySelectorAll('.settings-page > *'));
    for (let index = 1; index < sections.length; index += 1) {
      const between =
        sections[index]!.getBoundingClientRect().top - sections[index - 1]!.getBoundingClientRect().bottom;
      expect(between, `gap before settings section ${index}`).toBeLessThanOrEqual(16 + TOLERANCE_PX);
    }
  });

  it.each(PHONE_VIEWPORTS)('adds one compact inset per nested card level at %spx', async (width) => {
    await page.viewport(width, 800);
    await renderProvidersPage();
    const profiles = document.querySelector('.provider-profiles')!;
    const identityInput = document.querySelector('.provider-profile-identity-grid input')!;
    const tierSelect = document.querySelector('.provider-tier-card select')!;
    const secretsCard = screen.getByText('Managed Secrets').closest('.settings-page > *')!;
    const secretSlug = document.getElementById('secSlug')!;

    // Section card -> Identity fieldset -> field.
    expectInsetAtMost(identityInput, profiles, 2 * LEVEL_INSET_PX, 'Profile identity field');
    // Section card -> tier editor fieldset -> tier card -> field.
    expectInsetAtMost(tierSelect, profiles, 3 * LEVEL_INSET_PX, 'tier model field');
    // Managed Secrets card -> Add secret panel -> field.
    expectInsetAtMost(secretSlug, secretsCard, 2 * LEVEL_INSET_PX, 'secret slug field');
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
  });

  it('keeps the desktop card padding', async () => {
    await page.viewport(1280, 800);
    await renderProvidersPage();
    const profiles = document.querySelector('.provider-profiles')!;
    const identityInput = document.querySelector('.provider-profile-identity-grid input')!;
    const { left } = inlineInsets(identityInput, profiles);
    // Desktop keeps the roomy 1.5rem section and 1.25rem fieldset padding.
    expect(left).toBeGreaterThanOrEqual(24 + 20 - TOLERANCE_PX);
  });
});
