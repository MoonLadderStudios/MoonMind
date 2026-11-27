import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { commands, page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { CREATE_SUBMIT_BAR_FOCUS_SETTLE_MS } from '../lib/createFloatingSubmitBar';
import { configure, renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

declare module 'vitest/browser' {
  interface BrowserCommands {
    replaceInputByKeyboard: (label: string, text: string) => Promise<void>;
    emulateReducedMotion: (reducedMotion: 'reduce' | 'no-preference' | null) => Promise<void>;
  }
}

// MoonMind#5471: on a phone the Create submit bar must not cover the field
// being edited. It hides while a form field has focus, docks below the last
// fields at the end of the form, and collapses to one summary line.

configure({ asyncUtilTimeout: 5_000 });

const PHONE = { width: 390, height: 844 } as const;
const DESKTOP = { width: 1280, height: 800 } as const;
const SUMMARY_NAME = /^Repository and publish mode:/;
const BLANK_SOURCE_NOTICE =
  'PR publishing needs a repository. Add one, or choose None to keep results save-only.';
const NETWORK_FAILURE = /Couldn't reach the workflow start service/;

const uiInfo = {
  app: 'moonmind',
  buildId: 'create-floating-bar-browser-test',
  apiBase: '/api',
  features: { workflowList: true, workflowActions: true, workflowLiveUpdates: false, artifacts: true },
  limits: {},
  endpoints: {},
  dashboardConfig: {
    pollIntervalsMs: { list: 60_000, detail: 60_000, events: 60_000 },
    sources: {
      temporal: { create: '/api/executions', artifactCreate: '/api/artifacts' },
      github: { branches: '/api/github/branches?repository={repository}' },
    },
    system: {
      defaultRepository: 'MoonLadderStudios/MoonMind',
      defaultAgentRuntime: 'codex_cli',
      defaultTaskModel: 'gpt-5.6-codex',
      defaultTaskEffort: 'high',
      defaultPublishMode: 'pr',
      supportedAgentRuntimes: ['codex_cli', 'claude_code'],
      providerProfiles: { list: '/api/v1/provider-profiles' },
      presetCatalog: { enabled: true, list: '/api/presets' },
    },
    features: {
      temporalDashboard: { actionsEnabled: true, workspaceShellEnabled: false, temporalWorkflowEditing: true },
    },
  },
};

const payload: BootPayload = { page: 'dashboard', apiBase: '/api' };

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;
let createRequests: Array<Record<string, unknown>>;
let failNextCreate: boolean;

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/new');
  createRequests = [];
  failNextCreate = false;
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/ui/info') return jsonResponse(uiInfo);
      if (url === '/api/executions' && init?.method === 'POST') {
        if (failNextCreate) {
          failNextCreate = false;
          throw new TypeError('Failed to fetch');
        }
        createRequests.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return jsonResponse({ workflowId: 'mm:created', runId: 'created-run' }, 201);
      }
      if (url.startsWith('/api/v1/provider-profiles')) {
        return jsonResponse([
          {
            profile_id: 'oauth-1',
            account_label: 'Codex OAuth',
            provider_id: 'openai',
            default_model: 'gpt-5.6-codex',
            default_effort: 'high',
            enabled: true,
            launch_ready: true,
          },
        ]);
      }
      if (url.startsWith('/api/github/branches')) {
        return jsonResponse({
          items: [
            { value: 'main', label: 'main', source: 'github' },
            { value: 'feature/mobile', label: 'feature/mobile', source: 'github' },
          ],
          defaultBranch: 'main',
          error: null,
        });
      }
      if (url.startsWith('/api/workflows/skills')) return jsonResponse({ items: {} });
      if (url.startsWith('/api/presets')) return jsonResponse({ items: [] });
      if (url.startsWith('/api/artifacts')) return jsonResponse({ artifacts: [] });
      if (url.startsWith('/api/executions')) return jsonResponse({ items: [] });
      return jsonResponse({});
    },
  );
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/');
  window.scrollTo(0, 0);
  await commands.emulateReducedMotion(null);
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

async function renderCreate(viewport: { width: number; height: number }) {
  await page.viewport(viewport.width, viewport.height);
  const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
  cleanupRender = unmount;
  const instructions = (await screen.findByLabelText(
    'Instructions',
    {},
    { timeout: 10_000 },
  )) as HTMLTextAreaElement;
  const bar = screen.getByRole('group', { name: 'Workflow submission controls' });
  const start = within(bar).getByRole('button', { name: 'Start Workflow' }) as HTMLButtonElement;
  await waitFor(() => expect(start.disabled).toBe(false));
  // The panel's entry animation transforms an ancestor, which anchors the
  // fixed bar to the panel until it ends.
  const finite = document
    .getAnimations()
    .filter((animation) => animation.effect?.getComputedTiming().endTime !== Infinity);
  await Promise.all(finite.map((animation) => animation.finished.catch(() => undefined)));
  window.scrollTo(0, 0);
  await expect.poll(() => bar.dataset.placement, POLL).toBe('floating');
  return { instructions, bar, start };
}

const POLL = { timeout: 5_000 } as const;
const viewportHeight = () => document.documentElement.clientHeight;
const viewportWidth = () => document.documentElement.clientWidth;

async function settled(element: Element) {
  await Promise.all(element.getAnimations().map((animation) => animation.finished.catch(() => undefined)));
}

function describeBox(element: Element) {
  const box = element.getBoundingClientRect();
  return JSON.stringify({ top: box.top, bottom: box.bottom, left: box.left, right: box.right, viewport: [viewportWidth(), viewportHeight()] });
}

function expectInViewport(element: Element) {
  const box = element.getBoundingClientRect();
  const state = describeBox(element);
  expect(box.width, state).toBeGreaterThan(0);
  expect(box.top, state).toBeGreaterThanOrEqual(-1);
  expect(box.left, state).toBeGreaterThanOrEqual(-1);
  expect(box.bottom, state).toBeLessThanOrEqual(viewportHeight() + 1);
  expect(box.right, state).toBeLessThanOrEqual(viewportWidth() + 1);
}

async function expectBarShown(bar: HTMLElement) {
  // The hide transition delays `visibility`, so wait for the state first.
  await expect.poll(() => bar.dataset.placement, POLL).not.toBe('hidden');
  await settled(bar);
  await expect.poll(() => getComputedStyle(bar).visibility, POLL).toBe('visible');
  await expect.poll(() => getComputedStyle(bar).opacity, POLL).toBe('1');
  expect(bar.hasAttribute('inert')).toBe(false);
  expectInViewport(bar);
}

async function expectBarHidden(bar: HTMLElement) {
  await expect.poll(() => bar.dataset.placement, POLL).toBe('hidden');
  await settled(bar);
  await expect.poll(() => getComputedStyle(bar).visibility, POLL).toBe('hidden');
  expect(bar.hasAttribute('inert')).toBe(true);
  expect(bar.getBoundingClientRect().top, describeBox(bar)).toBeGreaterThanOrEqual(viewportHeight());
}

function recordPlacements(bar: HTMLElement) {
  const placements: string[] = [];
  const observer = new MutationObserver(() => placements.push(bar.dataset.placement ?? ''));
  observer.observe(bar, { attributes: true, attributeFilter: ['data-placement'] });
  return { placements, stop: () => observer.disconnect() };
}

async function waitPastFocusSettle() {
  await new Promise((resolve) => window.setTimeout(resolve, CREATE_SUBMIT_BAR_FOCUS_SETTLE_MS + 100));
}

function dockState(bar: HTMLElement) {
  const slot = bar.previousElementSibling as HTMLElement;
  return JSON.stringify({
    placement: bar.dataset.placement,
    scrollY: window.scrollY,
    scrollHeight: document.documentElement.scrollHeight,
    clientHeight: viewportHeight(),
    slotTop: slot.getBoundingClientRect().top,
    slotSize: slot.style.blockSize,
    slotHidden: slot.hidden,
    barTop: bar.getBoundingClientRect().top,
    barHeight: bar.offsetHeight,
    barBottom: getComputedStyle(bar).bottom,
  });
}

async function scrollToFormEnd(bar: HTMLElement) {
  // Late data can still grow the page, so scroll to the end on each check.
  await waitFor(() => {
    window.scrollTo(0, document.documentElement.scrollHeight);
    expect(bar.dataset.placement, dockState(bar)).toBe('docked');
  });
}

function summaryToggle(bar: HTMLElement) {
  return within(bar).getByRole('button', { name: SUMMARY_NAME }) as HTMLButtonElement;
}

describe('Create floating submit bar on a phone', () => {
  it('hides while form fields are edited and returns on blur without flickering at 390px', async () => {
    const { instructions, bar, start } = await renderCreate(PHONE);
    await expectBarShown(bar);
    const fields = [
      instructions,
      screen.getByLabelText('Workflow Model'),
      screen.getByLabelText('Workflow Effort'),
      screen.getByLabelText('Harness'),
    ] as HTMLElement[];
    const record = recordPlacements(bar);

    for (const field of fields) {
      // A tap only reaches a field that is on screen.
      field.scrollIntoView({ block: 'center' });
      field.focus();
      expect(document.activeElement).toBe(field);
      await expectBarHidden(bar);
      // The field being edited is fully visible and nothing floats over it.
      expectInViewport(field);
      const box = field.getBoundingClientRect();
      const hit = document.elementFromPoint(box.left + box.width / 2, box.top + Math.min(box.height / 2, 12));
      expect(hit === field || field.contains(hit), describeBox(field)).toBe(true);
      // A hidden bar keeps its controls out of the tab order.
      start.focus();
      expect(document.activeElement).toBe(field);
    }
    await waitPastFocusSettle();
    record.stop();
    expect(record.placements).toEqual(['hidden']);

    // Closing the keyboard (blurring the field) brings the bar back.
    (document.activeElement as HTMLElement).blur();
    await expectBarShown(bar);
    start.focus();
    expect(document.activeElement).toBe(start);
  });

  it('stays visible while Repository, Branch or Publish Mode has focus at 390px', async () => {
    const { bar } = await renderCreate(PHONE);
    await userEvent.click(summaryToggle(bar));
    await expect.poll(() => bar.dataset.compact, POLL).toBe('expanded');
    for (const label of ['Repository', 'Branch', 'Publish Mode']) {
      const control = within(bar).getByLabelText(label) as HTMLElement;
      await waitFor(() => expect((control as HTMLInputElement).disabled).toBe(false));
      control.focus();
      expect(document.activeElement).toBe(control);
      await waitPastFocusSettle();
      await expectBarShown(bar);
      expectInViewport(control);
    }
  });

  it('docks below the last fields at the end of the form at 390px', async () => {
    const { bar } = await renderCreate(PHONE);
    expect(getComputedStyle(bar).position).toBe('fixed');

    await scrollToFormEnd(bar);
    expect(getComputedStyle(bar).position).toBe('relative');
    await expectBarShown(bar);
    const barBox = bar.getBoundingClientRect();
    const form = bar.closest('form')!;
    const controls = Array.from(
      form.querySelectorAll<HTMLElement>('input, select, textarea, button, summary, a[href]'),
      // Content of a closed <details> keeps a layout box but is not shown.
    ).filter((control) => !bar.contains(control) && control.checkVisibility({ visibilityProperty: true }));
    expect(controls.length).toBeGreaterThan(0);
    for (const control of controls) {
      // Every field and button ends above the docked bar: none is covered.
      expect(
        control.getBoundingClientRect().bottom,
        `${describeBox(control)} ${control.outerHTML.slice(0, 160)}`,
      ).toBeLessThanOrEqual(barBox.top + 1);
    }
    const advanced = screen.getByLabelText('Advanced mode');
    const advancedBox = advanced.getBoundingClientRect();
    expect(document.elementFromPoint(advancedBox.left + advancedBox.width / 2, advancedBox.top + advancedBox.height / 2))
      .toBe(advanced);

    // A docked bar covers nothing, so editing a field leaves it in place.
    screen.getByLabelText('Instructions').focus();
    await waitPastFocusSettle();
    await scrollToFormEnd(bar);
    (document.activeElement as HTMLElement).blur();

    window.scrollTo(0, 0);
    await expect.poll(() => bar.dataset.placement, POLL).toBe('floating');
    expect(getComputedStyle(bar).position).toBe('fixed');
    await expectBarShown(bar);
  });

  it('shows one summary line that expands to working controls at 390px', async () => {
    const { instructions, bar, start } = await renderCreate(PHONE);
    const toggle = summaryToggle(bar);
    const publish = within(bar).getByLabelText('Publish Mode') as HTMLSelectElement;
    const selectors = ['Repository', 'Branch', 'Publish Mode'].map(
      (label) => within(bar).getByLabelText(label) as HTMLElement,
    );

    expect(bar.dataset.compact).toBe('collapsed');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.textContent).toContain(`MoonMind · ${publish.selectedOptions[0]?.textContent?.trim()}`);
    const toggleBox = toggle.getBoundingClientRect();
    const startBox = start.getBoundingClientRect();
    // One line: the summary and Start share the row.
    expect(Math.abs(toggleBox.top + toggleBox.height / 2 - (startBox.top + startBox.height / 2))).toBeLessThan(4);
    expect(bar.getBoundingClientRect().height).toBeLessThan(startBox.height + toggleBox.height);
    expectInViewport(toggle);
    expectInViewport(start);
    for (const control of selectors) {
      // Collapsed controls are not rendered, so they cannot take focus.
      expect(control.getClientRects()).toHaveLength(0);
      control.focus();
      expect(document.activeElement).not.toBe(control);
    }
    toggle.focus();
    await userEvent.tab();
    expect(document.activeElement).toBe(start);

    await userEvent.click(toggle);
    await expect.poll(() => bar.dataset.compact, POLL).toBe('expanded');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    for (const control of selectors) expectInViewport(control);

    // Choose a branch and a publish mode in the expanded bar.
    await commands.replaceInputByKeyboard('Branch', 'feature/mobile');
    await userEvent.selectOptions(publish, 'branch');
    await expect.poll(() => bar.dataset.compact, POLL).toBe('collapsed');
    expect(document.activeElement).toBe(toggle);
    expect(toggle.textContent).toContain('MoonMind · Branch');

    // A tap outside the expanded bar collapses it too.
    await userEvent.click(toggle);
    await expect.poll(() => bar.dataset.compact, POLL).toBe('expanded');
    await userEvent.click(instructions);
    await expect.poll(() => bar.dataset.compact, POLL).toBe('collapsed');

    // Submit from the collapsed bar keeps both choices.
    await userEvent.fill(instructions, 'Keep the submit bar off the field on phones.');
    instructions.blur();
    await expectBarShown(bar);
    await userEvent.click(start);
    await waitFor(() => expect(createRequests).toHaveLength(1));
    const submitted = createRequests[0]?.payload as Record<string, unknown>;
    expect(submitted.publishMode).toBe('branch');
    expect(JSON.stringify(submitted)).toContain('"feature/mobile"');
  });

  it('keeps submit errors and the publish-mode error visible in the collapsed bar at 390px', async () => {
    const { instructions, bar, start } = await renderCreate(PHONE);
    const toggle = summaryToggle(bar);
    const repository = within(bar).getByLabelText('Repository') as HTMLInputElement;
    const publish = within(bar).getByLabelText('Publish Mode') as HTMLSelectElement;

    // An explicit PR choice stays selected when the repository is cleared.
    for (const mode of ['none', 'pr']) {
      await userEvent.click(toggle);
      await userEvent.selectOptions(publish, mode);
      await expect.poll(() => bar.dataset.compact, POLL).toBe('collapsed');
    }
    await userEvent.click(toggle);
    await commands.replaceInputByKeyboard('Repository', '');
    await userEvent.click(toggle);
    await expect.poll(() => bar.dataset.compact, POLL).toBe('collapsed');
    const notice = within(bar).getByText(BLANK_SOURCE_NOTICE);
    expectInViewport(notice);

    // A rejection naming Repository opens the bar on that field.
    await userEvent.fill(instructions, 'Report on the mobile bar.');
    instructions.blur();
    await expectBarShown(bar);
    await userEvent.click(start);
    const alert = await within(bar).findByRole('alert');
    expect(alert.textContent).toBe(BLANK_SOURCE_NOTICE);
    await waitFor(() => expect(document.activeElement).toBe(repository));
    expect(bar.dataset.compact).toBe('expanded');
    await expectBarShown(bar);
    expectInViewport(alert);
    expect(createRequests).toHaveLength(0);

    // The technical failure details stay in the collapsed bar.
    await commands.replaceInputByKeyboard('Repository', 'octo/repo');
    await userEvent.click(toggle);
    await expect.poll(() => bar.dataset.compact, POLL).toBe('collapsed');
    failNextCreate = true;
    await userEvent.click(start);
    const failure = await within(bar).findByText(NETWORK_FAILURE);
    expect(bar.dataset.compact).toBe('collapsed');
    await expectBarShown(bar);
    expectInViewport(failure);
    expect(bar.querySelector('.dashboard-error-details')).not.toBeNull();
    expectInViewport(within(bar).getByText('Technical details'));
  });

  it('hides without animation for reduced-motion users at 390px', async () => {
    await commands.emulateReducedMotion('reduce');
    const { instructions, bar } = await renderCreate(PHONE);
    expect(matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(true);
    instructions.focus();
    await expect.poll(() => bar.dataset.placement, POLL).toBe('hidden');
    expect(getComputedStyle(bar).visibility).toBe('hidden');
    expect(bar.getAnimations()).toHaveLength(0);
    instructions.blur();
    await expect.poll(() => bar.dataset.placement, POLL).toBe('floating');
    expect(getComputedStyle(bar).visibility).toBe('visible');
    expect(bar.getAnimations()).toHaveLength(0);
  });
});

describe('Create floating submit bar on a desktop', () => {
  it('keeps the full floating bar in place while fields are edited at 1280px', async () => {
    const { instructions, bar, start } = await renderCreate(DESKTOP);
    expect(bar.dataset.compact).toBeUndefined();
    expect(within(bar).queryByRole('button', { name: SUMMARY_NAME })).toBeNull();
    const style = getComputedStyle(bar);
    expect(style.position).toBe('fixed');
    expect(style.translate).toBe('none');
    expect(style.borderTopLeftRadius).toBe('24px');
    expect(style.backgroundColor).not.toBe('rgba(0, 0, 0, 0)');
    const barBox = bar.getBoundingClientRect();
    expect(
      Math.abs(viewportHeight() - barBox.bottom - Number.parseFloat(style.bottom)),
      JSON.stringify({ bar: describeBox(bar), bottom: style.bottom, innerHeight: window.innerHeight, scrollY: window.scrollY }),
    ).toBeLessThanOrEqual(1);
    // Repository, Branch and Publish Mode share one row (engines size a
    // select differently, so compare centres rather than tops).
    const rowCentres = ['Repository', 'Branch', 'Publish Mode'].map((label) => {
      const box = within(bar).getByLabelText(label).getBoundingClientRect();
      return box.top + box.height / 2;
    });
    expect(
      Math.max(...rowCentres) - Math.min(...rowCentres),
      JSON.stringify({ rowCentres, bar: describeBox(bar) }),
    ).toBeLessThanOrEqual(3);
    expectInViewport(start);

    instructions.focus();
    await waitPastFocusSettle();
    expect(bar.dataset.placement).toBe('floating');
    await expectBarShown(bar);
  });

  it('keeps the first edit after a failed submit at 1280px', async () => {
    const { instructions, bar, start } = await renderCreate(DESKTOP);
    await userEvent.fill(instructions, 'Report on the desktop bar.');
    failNextCreate = true;
    await userEvent.click(start);
    await within(bar).findByText(NETWORK_FAILURE);
    // That first edit also dismisses the shown outcome; it must still land.
    await userEvent.fill(instructions, 'Retry with this edit.');
    expect(instructions.value).toBe('Retry with this edit.');
    await userEvent.click(start);
    await waitFor(() => expect(createRequests).toHaveLength(1));
    expect(JSON.stringify(createRequests[0])).toContain('Retry with this edit.');
  });
});
