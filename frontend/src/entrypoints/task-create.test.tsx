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
            legacyItems: [
              { id: 'speckit-orchestrate', markdown: '# Speckit Orchestrate' },
              { id: 'pr-resolver', markdown: '# PR Resolver' },
            ],
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
    fireEvent.change(screen.getByLabelText('Repository'), {
      target: { value: 'MoonLadderStudios/MoonMind' },
    });
    fireEvent.change(screen.getByLabelText('Skill'), {
      target: { value: 'speckit-orchestrate' },
    });

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
      payload: {
        repository: 'MoonLadderStudios/MoonMind',
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
        },
      },
    });
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith('/tasks/mm:workflow-123?source=temporal');
    });
  });

  it('updates provider-profile options when the selected runtime changes', async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const providerSelect = await screen.findByLabelText('Provider Profile');
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
    expect(request.payload.task.instructions).toBe('');
  });
});
