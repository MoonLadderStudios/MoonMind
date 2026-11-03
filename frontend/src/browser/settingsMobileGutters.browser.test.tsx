import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { MemoryRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { ProvidersSecretsSettingsPage } from '../entrypoints/settings';
import { renderWithClient, screen, within } from '../utils/test-utils';
import { contrast } from './colorContrast';
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
    // Keep visual fixtures independent of WebKit's shared history-write budget.
    <MemoryRouter initialEntries={['/settings/providers-secrets']}>
      <ProvidersSecretsSettingsPage payload={payload} />
    </MemoryRouter>,
    { container: panel },
  );
  // The profile editor opens from Add profile rather than always rendering.
  (await screen.findByRole('button', { name: 'Add profile' })).click();
  await screen.findByRole('list', { name: 'Model and effort tiers' });
  await screen.findByText('Managed Secrets');
}

beforeEach(() => {
  document.body.style.margin = '0';
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles')) {
        return jsonResponse([savedProfile]);
      }
      if (url.startsWith('/api/v1/secrets')) {
        return jsonResponse({ items: [{ slug: 'github-token', status: 'active', details: {}, createdAt: '2026-10-17T00:00:00Z' }] });
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
  document.documentElement.classList.remove('dark');
  await page.viewport(1280, 800);
});

describe('Providers & Secrets phone gutters', () => {
  it('renders without changing the browser navigation state', async () => {
    const navigation = {
      url: window.location.href,
      state: window.history.state,
      length: window.history.length,
    };

    await renderProvidersPage();

    expect({
      url: window.location.href,
      state: window.history.state,
      length: window.history.length,
    }).toEqual(navigation);
  });
  it('keeps removal of an unsaved tier neutral', async () => {
    await renderProvidersPage();
    await userEvent.click(screen.getByRole('button', { name: 'Duplicate Tier 1 as new last tier' }));
    const remove = screen.getByRole('button', { name: 'Remove Tier 2' });
    expect(getComputedStyle(remove).color).toBe(getComputedStyle(document.body).color);
    expect(getComputedStyle(remove).backgroundColor).toBe('rgba(0, 0, 0, 0)');
  });
  it.each(['light', 'dark'])('keeps actual profile and secret actions readable with focus during hover (%s)', async (theme) => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    await renderProvidersPage();
    await screen.findByRole('button', { name: 'Copy SecretRef for github-token' });
    const profileButtons = [...panel.querySelectorAll<HTMLButtonElement>('.provider-profiles table button:not(:disabled)')];
    const secretButtons = [...panel.querySelectorAll<HTMLButtonElement>('.managed-secrets-actions button:not(:disabled)')];
    expect(profileButtons.map((button) => button.getAttribute('aria-label'))).toEqual(['Actions for codex_team_oauth']);
    expect(secretButtons.length).toBeGreaterThanOrEqual(3);
    const surface = theme === 'dark' ? 'rgb(20, 18, 34)' : 'rgb(255, 255, 255)';
    const expectReadable = (elements: HTMLElement[]) => {
      for (const element of elements) {
        const background: string[] = [];
        for (let parent: Element | null = element; parent; parent = parent.parentElement) background.unshift(getComputedStyle(parent).backgroundColor);
        expect(contrast(getComputedStyle(element).color, surface, ...background), element.textContent ?? '').toBeGreaterThanOrEqual(4.5);
      }
    };
    expectReadable([...profileButtons, ...secretButtons]);
    await userEvent.click(profileButtons[0]!);
    const menuItems = screen.getAllByRole('menuitem');
    expect(menuItems.map((item) => item.getAttribute('aria-label'))).toContain('Delete');
    expectReadable(menuItems);
    await userEvent.keyboard('{Escape}');
    expect(document.activeElement).toBe(profileButtons[0]);
    for (const button of [profileButtons[0]!, secretButtons[0]!]) {
      button.focus();
      await userEvent.keyboard('{Tab}{Shift>}{Tab}{/Shift}');
      await userEvent.hover(button);
      expect(document.activeElement).toBe(button);
      expect(getComputedStyle(button).outlineStyle).toBe('solid');
      expect(contrast(getComputedStyle(button).outlineColor, surface)).toBeGreaterThanOrEqual(3);
    }
  });
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
    expectInsetAtMost(header.querySelector('h1')!, header, LEVEL_INSET_PX, 'page title');

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
    // Section card -> Identity fieldset -> field.
    expectInsetAtMost(identityInput, profiles, 2 * LEVEL_INSET_PX, 'Profile identity field');
    // Section card -> tier editor fieldset -> tier card -> field.
    expectInsetAtMost(tierSelect, profiles, 3 * LEVEL_INSET_PX, 'tier model field');
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
  });

  it.each(PHONE_VIEWPORTS)('fits the focused Add secret dialog to the phone at %spx', async (width) => {
    await page.viewport(width, 800);
    await renderProvidersPage();
    await userEvent.click(screen.getByRole('button', { name: 'Add secret' }));
    const dialog = screen.getByRole('dialog', { name: 'Add secret' });
    const secretSlug = document.getElementById('secSlug')!;
    expect(document.activeElement).toBe(secretSlug);
    const box = dialog.getBoundingClientRect();
    expect(box.left).toBeGreaterThanOrEqual(-TOLERANCE_PX);
    expect(box.right).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
    expect(secretSlug.getBoundingClientRect().width, 'secret slug field width').toBeGreaterThanOrEqual(0.7 * width);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
  });

  it('moves focus to the profile editor from the single Edit action on a phone', async () => {
    await page.viewport(360, 800);
    await renderProvidersPage();
    await userEvent.click(screen.getByRole('button', { name: 'Actions for codex_team_oauth' }));
    const menu = screen.getByRole('menu', { name: 'codex_team_oauth actions' });
    const menuBox = menu.getBoundingClientRect();
    expect(menuBox.left, 'actions menu left edge').toBeGreaterThanOrEqual(-TOLERANCE_PX);
    expect(menuBox.right, 'actions menu right edge').toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
    const editActions = within(menu).getAllByRole('menuitem', { name: /Edit/ });
    expect(editActions.map((item) => item.textContent)).toEqual(['Edit']);

    await userEvent.click(editActions[0]!);
    const heading = await screen.findByRole('heading', { name: 'Edit Profile: codex_team_oauth' });
    await vi.waitFor(() => expect(document.activeElement).toBe(heading));
    const { top, bottom } = heading.getBoundingClientRect();
    expect(top).toBeGreaterThanOrEqual(-TOLERANCE_PX);
    expect(bottom).toBeLessThanOrEqual(window.innerHeight + TOLERANCE_PX);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
  });

  it.each([1280, 1920, 2560])('centers a bounded settings measure at %spx', async (width) => {
    await page.viewport(width, 900);
    await renderProvidersPage();
    const frame = document.querySelector<HTMLElement>('.settings-page')!;
    const box = frame.getBoundingClientRect();
    const shellBox = shell.getBoundingClientRect();
    const gutter = rootContentInset();
    // 72rem: the dashboard's shared readable page measure.
    expect(box.width, 'settings content width').toBeLessThanOrEqual(72 * 16 + TOLERANCE_PX);
    const leftGap = box.left - shellBox.left;
    const rightGap = shellBox.right - box.right;
    expect(Math.abs(leftGap - rightGap), 'centered settings content').toBeLessThanOrEqual(TOLERANCE_PX);
    expect(leftGap).toBeGreaterThanOrEqual(gutter - TOLERANCE_PX);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
  });

  it('shows the whole profile actions menu past the table edge on desktop', async () => {
    await page.viewport(1280, 800);
    await renderProvidersPage();
    await userEvent.click(screen.getByRole('button', { name: 'Actions for codex_team_oauth' }));
    const menu = screen.getByRole('menu', { name: 'codex_team_oauth actions' });
    const table = document.querySelector('.provider-profiles-table')!;
    // A single row is shorter than the menu, so the menu must overflow the table.
    expect(menu.getBoundingClientRect().bottom).toBeGreaterThan(table.getBoundingClientRect().bottom);
    menu.scrollIntoView({ block: 'center' });
    const box = menu.getBoundingClientRect();
    expect(box.bottom - table.getBoundingClientRect().bottom, 'menu extends past the table').toBeGreaterThan(8);
    // The menu's own lower edge, outside the table, is painted rather than clipped.
    const hit = document.elementFromPoint(box.left + box.width / 2, box.bottom - 3);
    expect(hit !== null && menu.contains(hit), `menu bottom edge hit ${hit?.outerHTML.slice(0, 80)}`).toBe(true);
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
