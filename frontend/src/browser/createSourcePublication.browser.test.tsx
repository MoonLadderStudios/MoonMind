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
      temporalDashboard: { actionsEnabled: true, listEnabled: false, workspaceShellEnabled: false, temporalWorkflowEditing: true },
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
let rejectNextCreate: boolean;
let updateRequests: Array<Record<string, unknown>>;

// A recorded anonymous public-repository run opened for edit.
const RECORDED_EDIT = {
  workflowId: 'mm:recorded-edit',
  workflowType: 'MoonMind.UserWorkflow',
  state: 'executing',
  targetRuntime: 'codex_cli',
  publishMode: 'none',
  inputParameters: {
    targetRuntime: 'codex_cli',
    repository: 'octo/public',
    publishMode: 'none',
    workspaceSpec: {
      workspaceSource: { kind: 'repository', repository: 'octo/public', accessMode: 'anonymous' },
    },
    workflow: {
      instructions: 'Summarize the public repository.',
      runtime: { mode: 'codex_cli' },
      publish: { mode: 'none' },
    },
  },
  actions: { canUpdateInputs: true, canRerun: false },
};

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/new');
  createRequests = [];
  rejectNextCreate = false;
  updateRequests = [];
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
      if (url === '/api/executions/mm%3Arecorded-edit?source=temporal') {
        return jsonResponse(RECORDED_EDIT);
      }
      if (url === '/api/executions/mm%3Arecorded-edit/update') {
        updateRequests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return jsonResponse(
          { detail: { code: 'invalid_execution_request', message: BACKEND_REJECTION } },
          422,
        );
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
  // Keep focus, selection and native typing in the same browser-provider action.
  await userEvent.type(input, `{selectall}{Backspace}${text}`);
  await waitFor(() => {
    const active = document.activeElement;
    const state = JSON.stringify({
      field: input.getAttribute('aria-label'),
      value: input.value,
      connected: input.isConnected,
      activeElement: active?.getAttribute('aria-label') || active?.tagName,
      documentHasFocus: document.hasFocus(),
      snapshotFrames: document.querySelectorAll('iframe.html2canvas-container').length,
    });
    expect(input.value, state).toBe(text);
    expect(active, state).toBe(input);
  });
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

      for (const control of [repository, branch, publish]) {
        const label = control.closest('label')!;
        const caption = within(label).getByText(control.getAttribute('aria-label')!);
        const fieldRect = control.getBoundingClientRect();
        const captionRect = caption.getBoundingClientRect();
        expect(captionRect.bottom).toBeLessThanOrEqual(fieldRect.top + 1);
        expect(fieldRect.width).toBeGreaterThanOrEqual(label.getBoundingClientRect().width - 2);
      }
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
      const rejection = await screen.findByText(BACKEND_REJECTION);
      // Focus moves to the reason so keyboard and screen-reader users hear it.
      await waitFor(() => expect(document.activeElement).toBe(rejection));
      expect(createRequests).toHaveLength(1);
      expect((createRequests[0]?.payload as Record<string, unknown>).repository).toBeUndefined();
      // Rejection keeps the draft for correction.
      expect(instructions.value).toBe('Write a scratch report.');
      expect(repository.value).toBe('');
      expect(publish.value).toBe('none');

      // A source the backend can never admit is caught before submission and
      // focus moves to the field that needs the correction.
      await replaceWithKeyboard(repository, 'octo repo');
      await waitFor(() => expect(start).not.toHaveAttribute('aria-busy', 'true'));
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

  for (const viewport of [DESKTOP, MOBILE]) {
    it(`keeps an edited anonymous source and focuses a rejection at ${viewport.width}px`, async () => {
      await page.viewport(viewport.width, viewport.height);
      window.history.replaceState({}, '', '/workflows/new?editExecutionId=mm%3Arecorded-edit');
      const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
      cleanupRender = unmount;

      const instructions = (await screen.findByLabelText(
        'Instructions',
        {},
        { timeout: 10_000 },
      )) as HTMLTextAreaElement;
      await waitFor(() => expect(instructions.value).toBe('Summarize the public repository.'));
      const anonymous = screen.getByLabelText(
        'Read without credentials (public repository)',
      ) as HTMLInputElement;
      const publish = screen.getByLabelText('Publish Mode') as HTMLSelectElement;
      const save = screen.getByRole('button', { name: 'Save Changes' });
      expect(anonymous.checked).toBe(true);
      expect(publish.value).toBe('none');
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 2);
      for (const control of [anonymous, publish, save]) {
        const rectangle = control.getBoundingClientRect();
        expect(rectangle.left).toBeGreaterThanOrEqual(-1);
        expect(rectangle.right).toBeLessThanOrEqual(window.innerWidth + 1);
      }

      await userEvent.fill(instructions, 'Summarize the public repository again.');
      await userEvent.click(save);
      const rejection = await screen.findByText(BACKEND_REJECTION);
      await waitFor(() => expect(document.activeElement).toBe(rejection));
      expect(updateRequests).toHaveLength(1);
      const patch = updateRequests[0]?.parametersPatch as Record<string, unknown>;
      expect(patch.workspace).toEqual({
        workspaceSource: { kind: 'repository', repository: 'octo/public', accessMode: 'anonymous' },
      });
      // Rejection keeps the edited draft and its source choice.
      expect(instructions.value).toBe('Summarize the public repository again.');
      expect(anonymous.checked).toBe(true);

      // The source choice stays keyboard operable.
      anonymous.focus();
      await userEvent.keyboard(' ');
      expect(anonymous.checked).toBe(false);
    });
  }
});
