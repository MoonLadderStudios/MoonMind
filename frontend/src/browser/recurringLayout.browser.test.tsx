import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { configure, renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import { resetDashboardPreferences } from '../utils/dashboardPreferences';
import '../styles/dashboard.css';

configure({ asyncUtilTimeout: 5000 });

const schedules = [
  {
    id: 'nightly', name: 'Nightly repository sweep', description: 'Keep the repository current.',
    enabled: true, version: 1, cron: '0 2 * * *', timezone: 'UTC',
    nextRunAt: '2026-11-19T02:00:00Z',
    target: { workflowType: 'MoonMind.UserWorkflow', initialParameters: {
      repository: { repository: { name: 'MoonLadderStudios/MoonMind' }, branch: { name: 'main' } },
      workflow: { runtime: { profileId: 'codex-primary', model: 'gpt-6', effort: 'high' } },
    } },
  },
  {
    id: 'weekly', name: 'Weekly dependency audit', enabled: false, version: 1,
    cron: '0 4 * * 1', timezone: 'America/New_York',
    target: { initialParameters: { repository: 'MoonLadderStudios/MoonMind', task: {
      runtime: { profileId: 'claude-review', modelTier: 2 },
    } } },
  },
];

const uiInfo = {
  features: { schedules: true, skills: true, workflowList: true, workflowActions: true, remediationCollection: true },
  endpoints: { schedules: '/api/recurring-workflows' },
  dashboardConfig: { pollIntervalsMs: { list: 60000, detail: 60000 } },
};

let unmount: (() => void) | undefined;

beforeEach(() => {
  document.body.style.margin = '0';
  window.localStorage.clear();
  resetDashboardPreferences();
  window.history.replaceState({}, '', '/schedules');
  vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
    const url = new URL(String(input), window.location.origin);
    const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
    if (url.pathname === '/api/ui/info') return json(uiInfo);
    if (url.pathname === '/api/v1/provider-profiles') return json([{ profile_id: 'codex-primary', enabled: true, launch_ready: true }]);
    if (url.pathname === '/api/recurring-workflows') return json({ items: schedules });
    if (url.pathname.endsWith('/runs')) return json({ items: [] });
    const selected = schedules.find(({ id }) => url.pathname === `/api/recurring-workflows/${id}`);
    if (selected) return json(selected);
    if (url.pathname === '/api/workflows/skills') return json({ legacyItems: [{ id: 'review-code', label: 'Review code', markdown: '# Review code' }] });
    return json({ items: [] });
  });
});

afterEach(async () => {
  unmount?.();
  unmount = undefined;
  vi.restoreAllMocks();
  document.documentElement.classList.remove('dark');
  window.localStorage.clear();
  resetDashboardPreferences();
  await page.viewport(1280, 800);
});

function mount() {
  const rendered = renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);
  unmount = rendered.unmount;
  // The browser harness serves source assets; the API normally owns /static.
  const logo = rendered.container.querySelector<HTMLImageElement>('.masthead-logo');
  if (logo) logo.src = new URL('../../../api_service/static/workflow_console/moonmindlogo.webp', import.meta.url).href;
  return rendered.container;
}

describe('Recurring layout and navigation', () => {
  for (const theme of ['light', 'dark']) {
    for (const width of [1440, 1024, 721, 720, 390, 320]) {
      it(`${theme} overview at ${width}px stays within the viewport`, async () => {
        await page.viewport(width, 900);
        document.documentElement.classList.toggle('dark', theme === 'dark');
        const container = mount();
        await screen.findByRole('link', { name: /Nightly repository sweep/ });
        expect(screen.queryByRole('button', { name: 'Filters' })).toBeNull();
        expect(screen.queryByRole('region', { name: 'Schedule summary' })).toBeNull();
        expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
        if (width > 720) {
          expect(screen.queryByRole('button', { name: 'Filter recurring schedules' })).toBeNull();
          const table = screen.getByRole('table', { name: 'Recurring schedules' });
          const slab = table.parentElement!;
          const header = table.querySelector('th')!;
          const masthead = container.querySelector('.masthead')!;
          // The shared panel briefly translates on entry; check its settled layout.
          await waitFor(() => expect(Math.abs(header.getBoundingClientRect().top - masthead.getBoundingClientRect().bottom)).toBeLessThan(1));
          expect(Math.abs(slab.getBoundingClientRect().left)).toBeLessThan(1);
          // Non-overlay scrollbars occupy part of the requested viewport.
          const contentWidth = document.documentElement.clientWidth;
          expect(Math.abs(slab.getBoundingClientRect().right - contentWidth)).toBeLessThan(1);
          expect(getComputedStyle(slab).borderTopWidth).toBe('0px');
          expect(getComputedStyle(slab).borderRadius).toBe('0px');
          expect(getComputedStyle(header).backgroundColor).not.toBe('rgba(0, 0, 0, 0)');
          expect(getComputedStyle(table.querySelector('tbody tr:nth-child(2)')!).backgroundColor).toBe('rgba(0, 0, 0, 0)');
          expect(within(table).getByText('codex-primary')).toBeTruthy();
          expect(within(table).getByText('gpt-6')).toBeTruthy();
          expect(within(table).getByText('Tier 2')).toBeTruthy();
          slab.scrollLeft = slab.scrollWidth;
          const action = within(table).getByRole('button', { name: 'Pause Nightly repository sweep' });
          expect(action.getBoundingClientRect().right).toBeLessThanOrEqual(width);
        } else {
          expect(screen.queryByRole('table', { name: 'Recurring schedules' })).toBeNull();
          expect(screen.getByRole('button', { name: 'Filter recurring schedules' })).toBeTruthy();
          const cards = screen.getByRole('list', { name: 'Recurring schedule cards' });
          for (const label of ['Provider Profile', 'Repository', 'Model', 'Effort']) {
            expect(within(cards).getAllByText(label)).toHaveLength(2);
          }
          for (const card of cards.children) {
            expect(card.getBoundingClientRect().left).toBeGreaterThanOrEqual(0);
            expect(card.getBoundingClientRect().right).toBeLessThanOrEqual(width);
          }
        }
        if (width === 1440 || width === 390) {
          await page.screenshot({ path: `../../../artifacts/browser-layout/recurring-${theme}-${width}.png` });
        }
      });
    }
  }

  it('navigates between table, sidebar, hidden detail, and Skills', async () => {
    await page.viewport(1440, 900);
    mount();
    await screen.findByRole('link', { name: /Nightly repository sweep/ });
    expect(screen.getByRole('link', { name: 'Create recurring schedule' }).getAttribute('href')).toBe('/workflows/new?scheduleMode=recurring');
    await userEvent.click(page.getByRole('link', { name: /Nightly repository sweep/ }));
    await screen.findByRole('heading', { name: 'Nightly repository sweep' });
    const sidebar = screen.getByRole('complementary', { name: 'Recurring schedule navigation' });
    expect(within(sidebar).queryByRole('searchbox')).toBeNull();
    expect(within(sidebar).getByRole('img', { name: 'Active' })).toBeTruthy();
    expect(within(sidebar).getByRole('img', { name: 'Paused' })).toBeTruthy();
    expect(sidebar.textContent).not.toContain('next');
    const create = within(sidebar).getByRole('link', { name: 'Create recurring schedule' });
    expect(create.getBoundingClientRect().right).toBeLessThanOrEqual(sidebar.getBoundingClientRect().right);
    const label = within(sidebar).getByText('Recurring');
    expect(create.getBoundingClientRect().left).toBeGreaterThan(label.getBoundingClientRect().right);
    expect(Math.abs(create.getBoundingClientRect().top - label.getBoundingClientRect().top)).toBeLessThan(15);
    expect(screen.getByText('Schedule', { selector: '.schedules-detail-context' })).toBeTruthy();
    await page.screenshot({ path: '../../../artifacts/browser-layout/recurring-sidebar.png' });
    await userEvent.click(page.getByRole('link', { name: /Weekly dependency audit/ }));
    await screen.findByRole('heading', { name: 'Weekly dependency audit' });
    expect(screen.getByRole('button', { name: 'Resume schedule' })).toBeTruthy();
    await userEvent.click(page.getByRole('radio', { name: 'No list', exact: true }));
    await waitFor(() => expect(screen.queryByRole('complementary', { name: 'Recurring schedule navigation' })).toBeNull());
    expect(screen.getByRole('heading', { name: 'Weekly dependency audit' })).toBeTruthy();
    await userEvent.click(page.getByRole('radio', { name: 'Full table', exact: true }));
    await screen.findByRole('table', { name: 'Recurring schedules' });
    expect(window.location.pathname).toBe('/schedules');
    const system = screen.getByRole('button', { name: /^(Recurring|System)$/ });
    if (system) await userEvent.click(system);
    expect(screen.queryByRole('menuitem', { name: 'Remediation' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Remediation' })).toBeNull();
    await userEvent.click(page.getByRole(system ? 'menuitem' : 'link', { name: 'Skills', exact: true }));
    await screen.findByText('Review code');
    expect(window.location.pathname).toBe('/skills');
  });

  it('filters an initially unfiltered mobile card list without a desktop toolbar', async () => {
    await page.viewport(390, 844);
    mount();
    await screen.findByRole('link', { name: /Nightly repository sweep/ });
    await userEvent.click(page.getByRole('button', { name: 'Filter recurring schedules' }));
    await userEvent.fill(page.getByRole('textbox', { name: 'Schedule filter value' }), 'nightly');
    await waitFor(() => expect(window.location.search).toContain('schedule=nightly'));
    await userEvent.click(page.getByRole('button', { name: 'Close filters' }));
    expect(screen.queryByRole('dialog', { name: 'Recurring filters' })).toBeNull();
    await userEvent.click(page.getByRole('button', { name: 'Remove Schedule filter' }));
    await waitFor(() => expect(window.location.search).not.toContain('schedule='));
    await userEvent.click(page.getByRole('button', { name: 'Filter recurring schedules' }));
    expect(screen.getByRole('dialog', { name: 'Recurring filters' })).toBeTruthy();
  });

  it('opens standalone detail on mobile without a hidden sidebar', async () => {
    await page.viewport(390, 844);
    mount();
    await screen.findByRole('link', { name: /Nightly repository sweep/ });
    await userEvent.click(page.getByRole('link', { name: /Nightly repository sweep/ }));
    await screen.findByRole('heading', { name: 'Nightly repository sweep' });
    expect(screen.queryByRole('complementary', { name: 'Recurring schedule navigation' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Edit schedule' })).toBeTruthy();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
    await page.screenshot({ path: '../../../artifacts/browser-layout/recurring-mobile-detail.png' });
  });
});
