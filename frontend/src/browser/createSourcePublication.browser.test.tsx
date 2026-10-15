import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser Create journey for workspace source and publication intent
// (MoonLadderStudios/MoonMind#2619). It mounts the production dashboard router
// at /workflows/new and captures the ordinary POST /api/executions request.

const DESKTOP = { width: 1280, height: 900 } as const;
const MOBILE = { width: 390, height: 844 } as const;
const REPOSITORY_ERROR =
  'Repository must be a repository name or Git URL without spaces, embedded credentials, or local paths.';
const BACKEND_REJECTION =
  'payload.workflow.instructions is too long for inline submission; attach the content as a file.';

const uiInfo = {
  app: 'moonmind',
  buildId: 'create-source-browser-test',
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
      temporalDashboard: { actionsEnabled: true, listEnabled: false, workspaceShellEnabled: false },
    },
  },
  settingsPermissions: [],
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
let rejectNextCreate: boolean;

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/new');
  createRequests = [];
  rejectNextCreate = false;
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/ui/info') return jsonResponse(uiInfo);
      if (url === '/api/executions' && init?.method === 'POST') {
        createRequests.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        if (rejectNextCreate) {
          rejectNextCreate = false;
          return jsonResponse(
            { detail: { code: 'invalid_workflow_request', message: BACKEND_REJECTION } },
            422,
          );
        }
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
          items: [{ value: 'main', label: 'main', source: 'github' }],
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
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

async function replaceWithKeyboard(input: HTMLInputElement, text: string) {
  input.focus();
  input.setSelectionRange(0, input.value.length);
  await userEvent.keyboard(text ? `{Backspace}${text}` : '{Backspace}');
}

function publicationOutcome(select: HTMLSelectElement): string {
  return document.getElementById(select.getAttribute('aria-describedby') || '')?.textContent || '';
}

describe('Create workspace source and publication intent', () => {
  for (const viewport of [DESKTOP, MOBILE]) {
    it(`authors scratch and repository work by keyboard at ${viewport.width}px`, async () => {
      await page.viewport(viewport.width, viewport.height);
      const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
      cleanupRender = unmount;

      const instructions = (await screen.findByLabelText(
        'Instructions',
        {},
        { timeout: 10_000 },
      )) as HTMLTextAreaElement;
      const repository = screen.getByLabelText('Repository') as HTMLInputElement;
      const branch = screen.getByLabelText('Branch') as HTMLInputElement;
      const publish = screen.getByLabelText('Publish Mode') as HTMLSelectElement;
      const start = screen.getByRole('button', { name: 'Start Workflow' });

      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 2);
      for (const control of [repository, branch, publish, start]) {
        const rectangle = control.getBoundingClientRect();
        expect(rectangle.left).toBeGreaterThanOrEqual(-1);
        expect(rectangle.right).toBeLessThanOrEqual(window.innerWidth + 1);
      }

      // Clearing the source is ordinary scratch work: save-only, and no
      // destination mode is offered as a selectable dead end.
      await replaceWithKeyboard(repository, '');
      await waitFor(() => expect(publish.value).toBe('none'));
      expect(publicationOutcome(publish)).toBe(
        'Saves results on this workflow; nothing is published.',
      );
      expect(within(publish).getByRole('option', { name: 'Branch' })).toBeDisabled();
      expect(within(publish).getByRole('option', { name: 'PR' })).toBeDisabled();

      await userEvent.fill(instructions, 'Write a scratch report.');
      rejectNextCreate = true;
      await userEvent.click(start);
      expect(await screen.findByText(BACKEND_REJECTION)).toBeTruthy();
      expect(createRequests).toHaveLength(1);
      expect((createRequests[0]?.payload as Record<string, unknown>).repository).toBeUndefined();
      // Rejection keeps the draft for correction.
      expect(instructions.value).toBe('Write a scratch report.');
      expect(repository.value).toBe('');
      expect(publish.value).toBe('none');

      // A source the backend can never admit is caught before submission and
      // focus moves to the field that needs the correction.
      await replaceWithKeyboard(repository, 'octo repo');
      await userEvent.click(start);
      expect(await screen.findByText(REPOSITORY_ERROR)).toBeTruthy();
      expect(document.activeElement).toBe(repository);
      expect(createRequests).toHaveLength(1);

      await replaceWithKeyboard(repository, 'octo/repo');
      await userEvent.selectOptions(publish, 'pr');
      expect(publicationOutcome(publish)).toBe('Opens a pull request on octo/repo.');
      await userEvent.click(start);
      await waitFor(() => expect(createRequests).toHaveLength(2));
      const submitted = (createRequests[1]?.payload as Record<string, unknown>).repository;
      const submittedName =
        typeof submitted === 'string'
          ? submitted
          : ((submitted as { repository?: { name?: string } }).repository?.name ?? '');
      expect(submittedName).toBe('octo/repo');
      expect((createRequests[1]?.payload as Record<string, unknown>).publishMode).toBe('pr');
    });
  }
});
