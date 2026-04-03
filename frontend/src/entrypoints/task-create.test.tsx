import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { navigateTo } from '../lib/navigation';
import { renderWithClient } from '../utils/test-utils';
import { TaskCreatePage } from './task-create';

vi.mock('../lib/navigation', () => ({
  navigateTo: vi.fn(),
}));

const mockPayload: BootPayload = {
  page: 'task-create',
  apiBase: '/api',
  initialData: {
    dashboardConfig: {
      sources: {
        temporal: {
          create: '/api/executions',
          artifactCreate: '/api/artifacts',
        },
      },
      system: {
        defaultRepository: 'MoonLadderStudios/MoonMind',
        defaultTaskRuntime: 'codex_cli',
        defaultTaskModel: 'gpt-5.4',
        defaultTaskEffort: 'medium',
        defaultPublishMode: 'pr',
        defaultProposeTasks: false,
        defaultTaskModelByRuntime: {
          codex_cli: 'gpt-5.4',
          gemini_cli: 'gemini-2.5-pro',
          claude_code: 'claude-3.7-sonnet',
        },
        defaultTaskEffortByRuntime: {
          codex_cli: 'medium',
          gemini_cli: 'high',
          claude_code: 'low',
        },
        supportedTaskRuntimes: ['codex_cli', 'gemini_cli', 'claude_code'],
        providerProfiles: {
          list: '/api/v1/provider-profiles',
        },
        taskTemplateCatalog: {
          enabled: true,
          list: '/api/task-step-templates',
          detail: '/api/task-step-templates/{slug}',
          expand: '/api/task-step-templates/{slug}:expand',
        },
      },
    },
  },
};

describe('Task Create Entrypoint', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Task Create', '/tasks/new');
    vi.mocked(navigateTo).mockReset();
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith('/api/tasks/skills')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: { worker: ['speckit-orchestrate', 'pr-resolver'] },
          }),
        } as Response);
      }
      if (url.startsWith('/api/task-step-templates?scope=personal')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [],
          }),
        } as Response);
      }
      if (url.startsWith('/api/task-step-templates?scope=global')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                slug: 'speckit-demo',
                scope: 'global',
                title: 'Spec Kit Demo',
                description: 'Seed a two-step planning flow.',
                latestVersion: '1.2.3',
                version: '1.2.3',
              },
            ],
          }),
        } as Response);
      }
      if (url.startsWith('/api/task-step-templates/speckit-demo?scope=global')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            slug: 'speckit-demo',
            scope: 'global',
            title: 'Spec Kit Demo',
            description: 'Seed a two-step planning flow.',
            latestVersion: '1.2.3',
            version: '1.2.3',
            inputs: [
              {
                name: 'feature_name',
                label: 'Feature Name',
                type: 'text',
                required: true,
              },
            ],
          }),
        } as Response);
      }
      if (url.startsWith('/api/task-step-templates/speckit-demo:expand?scope=global')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            steps: [
              {
                id: 'tpl:speckit-demo:1.2.3:01',
                title: 'Clarify spec',
                instructions: 'Clarify the {{ inputs.feature_name }} scope.',
                skill: {
                  id: 'speckit-clarify',
                  args: { feature: 'Task Create' },
                },
              },
              {
                id: 'tpl:speckit-demo:1.2.3:02',
                title: 'Plan implementation',
                instructions: 'Write a plan for the task builder recovery.',
              },
            ],
            appliedTemplate: {
              slug: 'speckit-demo',
              version: '1.2.3',
            },
            warnings: [],
          }),
        } as Response);
      }
      if (url.startsWith('/api/v1/provider-profiles')) {
        const runtimeId = new URL(`http://localhost${url}`).searchParams.get('runtime_id');
        const items =
          runtimeId === 'gemini_cli'
            ? [{ profile_id: 'profile:gemini-default', account_label: 'Gemini Default' }]
            : runtimeId === 'claude_code'
              ? [{ profile_id: 'profile:claude-default', account_label: 'Claude Default' }]
              : [
                  { profile_id: 'profile:codex-default', account_label: 'Codex Default' },
                  { profile_id: 'profile:codex-secondary', account_label: 'Codex Secondary' },
                ];
        return Promise.resolve({
          ok: true,
          json: async () => items,
        } as Response);
      }
      if (url === '/api/executions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            workflowId: 'mm:workflow-123',
            runId: 'run-123',
            namespace: 'moonmind',
            redirectPath: '/tasks/mm:workflow-123?source=temporal',
          }),
        } as Response);
      }
      if (url === '/api/artifacts') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifact_ref: {
              artifact_id: 'art-001',
            },
            upload: {
              mode: 'single',
              upload_url: '/api/artifacts/art-001/content',
              expires_at: '2026-04-02T00:00:00Z',
              max_size_bytes: 100000,
              required_headers: {},
            },
          }),
        } as Response);
      }
      if (url === '/api/artifacts/art-001/content') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifact_id: 'art-001' }),
        } as Response);
      }
      if (url === '/api/artifacts/art-001/links') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifact_id: 'art-001' }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: `Unhandled fetch for ${url} ${String(init?.method || 'GET')}`,
        text: async () => 'Unhandled fetch',
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('submits the queue-shaped Temporal task payload and redirects on success', async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText('Instructions'), {
      target: { value: 'Run end-to-end regression flow.' },
    });
    fireEvent.change(document.querySelector('input[name="repository"]') as HTMLInputElement, {
      target: { value: 'MoonLadderStudios/MoonMind' },
    });
    fireEvent.change(
      document.querySelector('input[data-step-field="skillId"][data-step-index="0"]') as HTMLInputElement,
      {
      target: { value: 'speckit-orchestrate' },
      },
    );

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions',
        expect.objectContaining({
          method: 'POST',
        }),
      );
    });

    const executionCall = fetchSpy.mock.calls.filter(([url]) => String(url) === '/api/executions').at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request).toMatchObject({
      type: 'task',
      priority: 0,
      maxAttempts: 3,
      payload: {
        repository: 'MoonLadderStudios/MoonMind',
        targetRuntime: 'codex_cli',
        task: {
          instructions: 'Run end-to-end regression flow.',
          tool: {
            type: 'skill',
            name: 'speckit-orchestrate',
            version: '1.0',
          },
          runtime: {
            mode: 'codex_cli',
            model: 'gpt-5.4',
            effort: 'medium',
          },
          publish: {
            mode: 'pr',
          },
          proposeTasks: false,
        },
      },
    });
    expect(request.payload.requiredCapabilities).toEqual(['codex_cli', 'git', 'gh']);
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith('/tasks/mm:workflow-123?source=temporal');
    });
  });

  it('renders the restored legacy create-task controls', async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByPlaceholderText('owner/repo')).not.toBeNull();
    expect(await screen.findByLabelText('Provider profile')).not.toBeNull();
    expect(await screen.findByLabelText('Feature Request / Initial Instructions')).not.toBeNull();
    expect(await screen.findByPlaceholderText('auto-generated unless starting branch is non-default')).not.toBeNull();
    expect(await screen.findByDisplayValue('3')).not.toBeNull();
    expect(screen.getByText('Task Presets (optional)')).not.toBeNull();
    expect(screen.getByText('Schedule (optional)')).not.toBeNull();
  });

  it('updates provider-profile options when the selected runtime changes', async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const providerSelect = await screen.findByLabelText('Provider profile');
    await waitFor(() => {
      const labels = Array.from((providerSelect as HTMLSelectElement).options).map((option) => option.text);
      expect(labels).toEqual(['Default (system chooses)', 'Codex Default', 'Codex Secondary']);
    });

    fireEvent.change(screen.getByLabelText('Runtime'), {
      target: { value: 'gemini_cli' },
    });

    await waitFor(() => {
      const labels = Array.from((providerSelect as HTMLSelectElement).options).map((option) => option.text);
      expect(labels).toEqual(['Default (system chooses)', 'Gemini Default']);
    });
  });

  it('uploads oversized instructions as an artifact before submitting the execution', async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText('Instructions'), {
      target: { value: 'Large instructions '.repeat(1000) },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/artifacts',
        expect.objectContaining({ method: 'POST' }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/artifacts/art-001/content',
        expect.objectContaining({ method: 'PUT' }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/artifacts/art-001/links',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    const executionCall = fetchSpy.mock.calls.filter(([url]) => String(url) === '/api/executions').at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.inputArtifactRef).toBe('art-001');
    expect(request.payload.task.instructions).toBeUndefined();
  });

  it('applies a preset into task steps and submits them', async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const presetSelect = await screen.findByLabelText('Preset');
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === 'Spec Kit Demo (Global)',
        ),
      ).toBe(true);
    });

    fireEvent.change(presetSelect, {
      target: { value: 'global::::speckit-demo' },
    });

    fireEvent.change(screen.getByLabelText('Feature Request / Initial Instructions'), {
      target: { value: 'Task Create' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    await screen.findByDisplayValue('Clarify the {{ inputs.feature_name }} scope.');
    await screen.findByDisplayValue('Write a plan for the task builder recovery.');

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions',
        expect.objectContaining({
          method: 'POST',
        }),
      );
    });

    const executionCall = fetchSpy.mock.calls.filter(([url]) => String(url) === '/api/executions').at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.instructions).toBe('Task Create');
    expect(request.payload.task.steps).toEqual([
      {
        id: 'tpl:speckit-demo:1.2.3:01',
        title: 'Clarify spec',
        instructions: 'Clarify the {{ inputs.feature_name }} scope.',
      },
      {
        id: 'tpl:speckit-demo:1.2.3:02',
        title: 'Plan implementation',
        instructions: 'Write a plan for the task builder recovery.',
      },
    ]);
    expect(request.payload.task.appliedStepTemplates).toEqual([
      expect.objectContaining({
        slug: 'speckit-demo',
        version: '1.2.3',
      }),
    ]);
  });
});
