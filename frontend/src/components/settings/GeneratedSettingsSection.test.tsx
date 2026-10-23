import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor, within } from '../../utils/test-utils';
import { renderWithClient } from '../../utils/test-utils';
import { GeneratedSettingsSection } from './GeneratedSettingsSection';

function ControlledGeneratedSettingsSection() {
  return <GeneratedSettingsSection />;
}

const workspaceCatalog = {
  section: 'user-workspace',
  categories: {
    Workflow: [
      {
        key: 'workflow.default_publish_mode',
        title: 'Default Publish Mode',
        description: 'Fallback publish mode used when tasks omit publish mode.',
        category: 'Workflow',
        section: 'user-workspace',
        type: 'enum',
        ui: 'select',
        default_value: 'pr',
        effective_value: 'pr',
        override_value: null,
        source: 'config_or_default',
        source_explanation: 'Resolved from application settings.',
        apply_mode: 'next_task',
        activation_state: 'pending_next_boundary',
        active: false,
        pending_value: 'pr',
        affected_process_or_worker: 'task_creation, publishing',
        completion_guidance: 'New tasks will use this value when they are created.',
        options: [
          { value: 'none', label: 'None' },
          { value: 'branch', label: 'Branch' },
          { value: 'pr', label: 'Pull Request' },
        ],
        constraints: null,
        sensitive: false,
        secret_role: null,
        read_only: false,
        read_only_reason: null,
        requires_reload: true,
        requires_worker_restart: false,
        requires_process_restart: false,
        applies_to: ['task_creation', 'publishing'],
        depends_on: [],
        order: 10,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 3,
        diagnostics: [],
      },
      {
        key: 'skills.canary_percent',
        title: 'Skills Canary Percent',
        description: 'Percentage of runs routed through skills-first policy.',
        category: 'Workflow',
        section: 'user-workspace',
        type: 'integer',
        ui: 'number',
        default_value: 100,
        effective_value: 25,
        override_value: null,
        source: 'instance_override',
        source_explanation: 'Resolved from an instance override.',
        apply_mode: 'next_task',
        activation_state: 'pending_next_boundary',
        active: false,
        pending_value: 25,
        affected_process_or_worker: 'skills',
        completion_guidance: 'New tasks will use this value when they are created.',
        options: null,
        constraints: { minimum: 0, maximum: 100 },
        sensitive: false,
        secret_role: null,
        read_only: false,
        read_only_reason: null,
        requires_reload: false,
        requires_worker_restart: false,
        requires_process_restart: false,
        applies_to: ['skills'],
        depends_on: [],
        order: 30,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 2,
        diagnostics: [],
      },
    ],
    Integrations: [
      {
        key: 'integrations.github.token_ref',
        title: 'GitHub Token Reference',
        description: 'Secret reference used for GitHub API access.',
        category: 'Integrations',
        section: 'user-workspace',
        type: 'secret_ref',
        ui: 'secret_ref_picker',
        default_value: null,
        effective_value: 'env://GITHUB_TOKEN',
        override_value: null,
        source: 'environment',
        source_explanation: 'Resolved from deployment environment.',
        apply_mode: 'next_launch',
        activation_state: 'pending_next_boundary',
        active: false,
        pending_value: 'env://GITHUB_TOKEN',
        affected_process_or_worker: 'github, integrations',
        completion_guidance: 'New launches will use this value the next time they start.',
        options: null,
        constraints: null,
        sensitive: false,
        secret_role: 'github_token',
        read_only: false,
        read_only_reason: null,
        requires_reload: false,
        requires_worker_restart: false,
        requires_process_restart: false,
        applies_to: ['github', 'integrations'],
        depends_on: [],
        order: 40,
        audit: { store_old_value: true, store_new_value: true, redact: true },
        value_version: 1,
        diagnostics: [
          {
            code: 'unresolved_secret_ref',
            message: 'integrations.github.token_ref references a managed secret that does not exist.',
            severity: 'error',
          },
        ],
      },
    ],
    Advanced: [
      {
        key: 'ui.density',
        title: 'UI Density',
        description: 'Preferred UI density.',
        category: 'Advanced',
        section: 'user-workspace',
        type: 'string',
        ui: 'text',
        default_value: 'comfortable',
        effective_value: 'comfortable',
        override_value: null,
        source: 'default',
        source_explanation: 'Resolved from default.',
        apply_mode: 'immediate',
        activation_state: 'active',
        active: true,
        pending_value: null,
        affected_process_or_worker: 'dashboard',
        completion_guidance: null,
        options: null,
        constraints: { min_length: 3, max_length: 24 },
        sensitive: false,
        secret_role: null,
        read_only: false,
        read_only_reason: null,
        requires_reload: false,
        requires_worker_restart: false,
        requires_process_restart: false,
        applies_to: ['dashboard'],
        depends_on: [],
        order: 50,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 1,
        diagnostics: [],
      },
      {
        key: 'notifications.channels',
        title: 'Notification Channels',
        description: 'Enabled notification channels.',
        category: 'Advanced',
        section: 'user-workspace',
        type: 'list',
        ui: 'tag_editor',
        default_value: [],
        effective_value: ['email'],
        override_value: null,
        source: 'default',
        source_explanation: 'Resolved from default.',
        apply_mode: 'immediate',
        activation_state: 'active',
        active: true,
        pending_value: null,
        affected_process_or_worker: 'notifications',
        completion_guidance: null,
        options: null,
        constraints: null,
        sensitive: false,
        secret_role: null,
        read_only: false,
        read_only_reason: null,
        requires_reload: false,
        requires_worker_restart: false,
        requires_process_restart: false,
        applies_to: ['notifications'],
        depends_on: [],
        order: 60,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 1,
        diagnostics: [],
      },
      {
        key: 'git.author_defaults',
        title: 'Git Author Defaults',
        description: 'Default Git author metadata.',
        category: 'Advanced',
        section: 'user-workspace',
        type: 'object',
        ui: 'key_value',
        default_value: {},
        effective_value: { name: 'MoonMind' },
        override_value: null,
        source: 'default',
        source_explanation: 'Resolved from default.',
        apply_mode: 'immediate',
        activation_state: 'active',
        active: true,
        pending_value: null,
        affected_process_or_worker: 'git',
        completion_guidance: null,
        options: null,
        constraints: null,
        sensitive: false,
        secret_role: null,
        read_only: false,
        read_only_reason: null,
        requires_reload: false,
        requires_worker_restart: false,
        requires_process_restart: false,
        applies_to: ['git'],
        depends_on: [],
        order: 70,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 1,
        diagnostics: [],
      },
      {
        key: 'operations.mode',
        title: 'Operator Locked Mode',
        description: 'Managed by operators.',
        category: 'Advanced',
        section: 'user-workspace',
        type: 'string',
        ui: 'readonly',
        default_value: 'normal',
        effective_value: 'normal',
        override_value: null,
        source: 'operator_locked',
        source_explanation: 'Resolved from operator policy.',
        apply_mode: 'manual_operation',
        activation_state: 'pending_manual_operation',
        active: false,
        pending_value: 'normal',
        affected_process_or_worker: 'operations',
        completion_guidance:
          'Use the related operation control to activate this value after coordinating affected operational workers and confirming no task launch is in progress.',
        options: null,
        constraints: null,
        sensitive: false,
        secret_role: null,
        read_only: true,
        read_only_reason: 'Operator locked by workspace policy.',
        requires_reload: false,
        requires_worker_restart: false,
        requires_process_restart: false,
        applies_to: ['operations'],
        depends_on: [],
        order: 80,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 1,
        diagnostics: [],
      },
      {
        key: 'operations.reset_mode',
        title: 'Pending Reset Mode',
        description: 'A reset waiting for restart.',
        category: 'Advanced',
        section: 'user-workspace',
        type: 'string',
        ui: 'readonly',
        default_value: null,
        effective_value: null,
        override_value: null,
        source: 'instance_override',
        source_explanation: 'Resolved from an instance override.',
        apply_mode: 'process_restart',
        activation_state: 'pending_restart',
        active: false,
        pending_value: null,
        affected_process_or_worker: 'api_service',
        completion_guidance: 'Restart the affected process to activate this value.',
        options: null,
        constraints: null,
        sensitive: false,
        secret_role: null,
        read_only: true,
        read_only_reason: null,
        requires_reload: false,
        requires_worker_restart: false,
        requires_process_restart: true,
        applies_to: ['api_service'],
        depends_on: [],
        order: 90,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 2,
        diagnostics: [],
      },
    ],
  },
};

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    statusText: ok ? 'OK' : 'Bad Request',
    json: async () => body,
  } as Response;
}

describe('GeneratedSettingsSection', () => {
  let fetchSpy: MockInstance;
  let requests: Array<{ url: string; init: RequestInit | undefined }>;

  beforeEach(() => {
    requests = [];
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input, init) => {
      const url = String(input);
      requests.push({ url, init });

      if (url.startsWith('/api/v1/settings/audit')) {
        return Promise.resolve(jsonResponse({
          items: [
            {
              id: 'audit-1',
              event_type: 'settings.override.updated',
              key: 'integrations.github.token_ref',
              actor_user_id: 'user-1',
              old_value: null,
              new_value: null,
              redacted: true,
              reason: 'Prefer branch publication',
              validation_outcome: 'accepted',
              affected_systems: ['publishing'],
              created_at: '2026-08-30T12:00:00Z',
            },
          ],
        }));
      }
      if (url.includes('/api/v1/settings/catalog')) {
        return Promise.resolve(jsonResponse(workspaceCatalog));
      }
      if (url === '/api/v1/settings') {
        return Promise.resolve(jsonResponse({ values: {} }));
      }
      return Promise.resolve(jsonResponse({ error: 'not_found', message: 'not found' }, false, 404));
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps activation and overrides visible while per-setting metadata sits in Details', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    const title = await screen.findByText('Skills Canary Percent');
    const row = title.closest<HTMLElement>('[data-setting-key]');
    expect(row?.getAttribute('data-setting-key')).toBe('skills.canary_percent');
    const details = row?.querySelector<HTMLDetailsElement>('details.setting-details');
    expect(details).not.toBeNull();
    expect(details?.open).toBe(false);

    const visible = within(row!);
    // Pending activation and the active instance override stay in the default view.
    expect(details?.contains(visible.getByText('Pending next boundary'))).toBe(false);
    expect(
      visible.getAllByText('Instance override').some((badge) => !details?.contains(badge)),
    ).toBe(true);
    expect(details?.contains(visible.getByText('Affects skills'))).toBe(false);
    // Source explanation, key and Applies-to targets are one disclosure away,
    // and the affected targets are no longer repeated as both text and chips.
    expect(details?.contains(visible.getByText('Resolved from an instance override.'))).toBe(true);
    expect(details?.contains(visible.getByText('skills.canary_percent'))).toBe(true);
    for (const target of visible.queryAllByText('skills')) {
      expect(details?.contains(target)).toBe(true);
    }
  });

  it('renders descriptor rows with metadata, badges, diagnostics, and filters', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    expect(await screen.findByText('Default Publish Mode')).toBeTruthy();
    expect(screen.getAllByText('Config')).not.toHaveLength(0);
    expect(screen.getAllByText('Applies on next task')).not.toHaveLength(0);
    expect(screen.getAllByText('Pending next boundary')).not.toHaveLength(0);
    expect(screen.getAllByText('New tasks will use this value when they are created.')).not.toHaveLength(0);
    expect(
      screen.getByText(
        'Use the related operation control to activate this value after coordinating affected operational workers and confirming no task launch is in progress.',
      ),
    ).toBeTruthy();
    expect(screen.getByText('Pending: Not set')).toBeTruthy();
    expect(screen.getByText('task_creation, publishing')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Search settings'), { target: { value: 'github' } });
    expect(screen.getByText('GitHub Token Reference')).toBeTruthy();
    expect(
      screen.getByText('integrations.github.token_ref references a managed secret that does not exist.'),
    ).toBeTruthy();
    expect(screen.queryByText('github-token-plaintext')).toBeNull();
    expect(screen.queryByText('Default Publish Mode')).toBeNull();

    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'Integrations' } });
    expect(screen.getByText('GitHub Token Reference')).toBeTruthy();
  });

  it('reads one instance catalog without scope inputs', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    expect(screen.queryByLabelText('Settings scope')).toBeNull();
    expect(screen.queryByRole('button', { name: 'User' })).toBeNull();
    const catalogRequests = requests.filter((request) => request.url.startsWith('/api/v1/settings/catalog'));
    expect(catalogRequests.map((request) => request.url)).toEqual([
      '/api/v1/settings/catalog?section=user-workspace',
    ]);
  });

  it('offers reset only for instance overrides and resets by key', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    await screen.findByText('Skills Canary Percent');
    expect(screen.queryByRole('button', { name: 'Reset Default Publish Mode' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reset Skills Canary Percent' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/settings/skills.canary_percent',
        expect.objectContaining({ method: 'DELETE' }),
      );
    });
  });

  it('edits generated controls and previews only changed keys', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');

    fireEvent.change(screen.getByLabelText('Default Publish Mode'), { target: { value: 'branch' } });
    fireEvent.change(screen.getByLabelText('Skills Canary Percent'), { target: { value: '50' } });
    fireEvent.change(screen.getByLabelText('UI Density'), { target: { value: 'compact' } });
    fireEvent.change(screen.getByLabelText('Notification Channels'), { target: { value: 'email,slack' } });
    fireEvent.change(screen.getByLabelText('Git Author Defaults'), { target: { value: 'name=MoonMind\\nemail=dev@example.com' } });
    fireEvent.change(screen.getByLabelText('GitHub Token Reference'), { target: { value: 'db://github-main' } });

    const preview = screen.getByLabelText('Pending settings preview');
    expect(within(preview).getByText('workflow.default_publish_mode')).toBeTruthy();
    expect(within(preview).getByText('skills.canary_percent')).toBeTruthy();
    expect(within(preview).getByText('ui.density')).toBeTruthy();
    expect(within(preview).getByText('notifications.channels')).toBeTruthy();
    expect(within(preview).getByText('git.author_defaults')).toBeTruthy();
    expect(within(preview).getByText('integrations.github.token_ref')).toBeTruthy();
    expect(within(preview).getAllByText('Valid')).not.toHaveLength(0);
    expect(screen.queryByText(/plaintext/i)).toBeNull();
  });

  it('saves only changed keys with expected versions and refreshes catalog', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    fireEvent.change(screen.getByLabelText('Default Publish Mode'), { target: { value: 'branch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/settings',
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({
            changes: { 'workflow.default_publish_mode': 'branch' },
            expected_versions: { 'workflow.default_publish_mode': 3 },
            reason: 'Updated from Settings.',
          }),
        }),
      );
    });
  });

  it('disables read-only descriptors and shows lock reason', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    expect(await screen.findByText('Operator Locked Mode')).toBeTruthy();
    expect(screen.getByText('Operator locked by workspace policy.')).toBeTruthy();
    expect((screen.getByLabelText('Operator Locked Mode') as HTMLInputElement).disabled).toBe(true);

    fireEvent.click(screen.getByLabelText('Read-only only'));
    expect(screen.getByText('Operator Locked Mode')).toBeTruthy();
    expect(screen.queryByText('Default Publish Mode')).toBeNull();
  });

  it('renders a Broken reference indicator on rows whose secret_ref is unresolved', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    expect(await screen.findByText('Default Publish Mode')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Search settings'), { target: { value: 'github' } });
    expect(screen.getByText('GitHub Token Reference')).toBeTruthy();
    expect(screen.getAllByText(/broken reference/i).length).toBeGreaterThan(0);
  });

  it('supports discard for pending generated setting changes', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    fireEvent.change(screen.getByLabelText('Default Publish Mode'), { target: { value: 'branch' } });
    expect(screen.getByLabelText('Pending settings preview')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(screen.queryByLabelText('Pending settings preview')).toBeNull();
  });

  it('loads row audit on demand with the setting key for the admitted operator', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    expect(requests.some((request) => request.url.startsWith('/api/v1/settings/audit'))).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'View audit for GitHub Token Reference' }));

    await waitFor(() => {
      const auditRequest = requests.find((request) => request.url.startsWith('/api/v1/settings/audit'));
      expect(auditRequest).toBeTruthy();
      const query = new URL(auditRequest?.url ?? '', window.location.origin).searchParams;
      expect(query.has('scope')).toBe(false);
      expect(query.get('key')).toBe('integrations.github.token_ref');
    });
    expect(await screen.findByRole('region', { name: 'Audit history for GitHub Token Reference' })).toBeTruthy();
    expect(await screen.findByText(/Prefer branch publication/)).toBeTruthy();
  });

  it('keeps long setting keys wrapped inside row Details, out of the action row', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    const keyLabel = screen.getByText('workflow.default_publish_mode');
    expect(keyLabel.className).toContain('break-all');
    expect(keyLabel.closest('details.setting-details')).not.toBeNull();
    const auditButton = screen.getByRole('button', { name: 'View audit for Default Publish Mode' });
    expect(auditButton.closest('div.flex')?.className).toContain('flex-wrap');
  });

  it('keeps a cleared number field empty and blocks Save when the setting has a default', async () => {
    renderWithClient(<ControlledGeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    const canary = screen.getByLabelText('Skills Canary Percent') as HTMLInputElement;
    fireEvent.change(canary, { target: { value: '' } });

    expect(canary.value).toBe('');
    const preview = screen.getByLabelText('Pending settings preview');
    expect(within(preview).getByText('Enter a number.')).toBeTruthy();
    expect((within(preview).getByRole('button', { name: 'Save changes' }) as HTMLButtonElement).disabled).toBe(true);
  });

  describe('optional settings without a default', () => {
    const profileDescriptor = {
      ...workspaceCatalog.categories.Workflow[0],
      key: 'workflow.default_provider_profile_ref',
      title: 'Default Provider Profile',
      description: 'Provider profile used when a request does not select one.',
      type: 'string',
      ui: 'provider_profile_picker',
      default_value: null,
      effective_value: 'codex-team',
      options: null,
      order: 70,
      value_version: 4,
    };
    const timeoutDescriptor = {
      ...workspaceCatalog.categories.Workflow[1],
      key: 'workflow.optional_timeout_seconds',
      title: 'Optional Timeout Seconds',
      default_value: null,
      effective_value: 30,
      constraints: { minimum: 1, maximum: null },
      order: 80,
      value_version: 5,
    };

    beforeEach(() => {
      fetchSpy.mockImplementation((input, init) => {
        const url = String(input);
        requests.push({ url, init });
        if (url.includes('/api/v1/settings/catalog')) {
          return Promise.resolve(jsonResponse({
            section: 'user-workspace',
            categories: { Workflow: [profileDescriptor, timeoutDescriptor] },
          }));
        }
        if (url === '/api/v1/provider-profiles') {
          return Promise.resolve(jsonResponse([
            { profile_id: 'codex-team', runtime_id: 'codex_cli', provider_id: 'openai', provider_label: 'OpenAI' },
            { profile_id: 'claude-team', runtime_id: 'claude_code', provider_id: 'anthropic', provider_label: 'Anthropic' },
          ]));
        }
        if (url === '/api/v1/settings') {
          return Promise.resolve(jsonResponse({ values: {} }));
        }
        return Promise.resolve(jsonResponse({ error: 'not_found' }, false, 404));
      });
    });

    it('picks the default Provider Profile from saved profiles', async () => {
      renderWithClient(<ControlledGeneratedSettingsSection />);

      const picker = await screen.findByRole('combobox', { name: 'Default Provider Profile' }) as HTMLSelectElement;
      await waitFor(() => expect(Array.from(picker.options).map((option) => option.value)).toEqual(['', 'codex-team', 'claude-team']));
      expect(picker.value).toBe('codex-team');
      expect(Array.from(picker.options).map((option) => option.textContent)).toEqual([
        'Not set',
        expect.stringContaining('codex-team'),
        expect.stringContaining('claude-team'),
      ]);

      fireEvent.change(picker, { target: { value: 'claude-team' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      await waitFor(() => {
        const save = requests.find((request) => request.init?.method === 'PATCH');
        expect(JSON.parse(String(save?.init?.body)).changes).toEqual({ 'workflow.default_provider_profile_ref': 'claude-team' });
      });
    });

    it('keeps a missing Provider Profile value visible instead of replacing it', async () => {
      fetchSpy.mockImplementation((input, init) => {
        const url = String(input);
        requests.push({ url, init });
        if (url.includes('/api/v1/settings/catalog')) {
          return Promise.resolve(jsonResponse({
            section: 'user-workspace',
            categories: { Workflow: [{ ...profileDescriptor, effective_value: 'retired-profile' }] },
          }));
        }
        if (url === '/api/v1/provider-profiles') return Promise.resolve(jsonResponse([]));
        return Promise.resolve(jsonResponse({ error: 'not_found' }, false, 404));
      });
      renderWithClient(<ControlledGeneratedSettingsSection />);

      const picker = await screen.findByRole('combobox', { name: 'Default Provider Profile' }) as HTMLSelectElement;
      await waitFor(() => expect(Array.from(picker.options).map((option) => option.value)).toEqual(['', 'retired-profile']));
      expect(picker.value).toBe('retired-profile');
      expect(screen.queryByLabelText('Pending settings preview')).toBeNull();
    });

    it('submits a cleared optional number as not set', async () => {
      renderWithClient(<ControlledGeneratedSettingsSection />);

      const timeout = await screen.findByLabelText('Optional Timeout Seconds') as HTMLInputElement;
      fireEvent.change(timeout, { target: { value: '' } });
      expect(timeout.value).toBe('');
      const preview = screen.getByLabelText('Pending settings preview');
      expect(within(preview).getByText('New: Not set')).toBeTruthy();
      fireEvent.click(within(preview).getByRole('button', { name: 'Save changes' }));
      await waitFor(() => {
        const save = requests.find((request) => request.init?.method === 'PATCH');
        expect(JSON.parse(String(save?.init?.body)).changes).toEqual({ 'workflow.optional_timeout_seconds': null });
      });
    });
  });

  it('keeps generated settings usable when an audit request fails', async () => {
    fetchSpy.mockImplementation((input, init) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.startsWith('/api/v1/settings/audit')) {
        return Promise.resolve(jsonResponse({}, false, 503));
      }
      if (url.includes('/api/v1/settings/catalog')) {
        return Promise.resolve(jsonResponse(workspaceCatalog));
      }
      return Promise.resolve(jsonResponse({ error: 'not_found' }, false, 404));
    });
    renderWithClient(<ControlledGeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    fireEvent.click(screen.getByRole('button', { name: 'View audit for Default Publish Mode' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Failed to fetch settings audit');
    expect(screen.getByText('Default Publish Mode')).toBeTruthy();
    expect(screen.getByLabelText('Default Publish Mode')).toBeTruthy();
  });
});
