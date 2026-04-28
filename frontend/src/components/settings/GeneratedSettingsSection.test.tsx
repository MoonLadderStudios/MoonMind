import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor, within } from '../../utils/test-utils';
import { renderWithClient } from '../../utils/test-utils';
import { GeneratedSettingsSection } from './GeneratedSettingsSection';

const workspaceCatalog = {
  section: 'user-workspace',
  scope: 'workspace',
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
        scopes: ['workspace'],
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
        key: 'live_sessions.default_enabled',
        title: 'Live Sessions Enabled By Default',
        description: 'Whether live task sessions are enabled by default.',
        category: 'Workflow',
        section: 'user-workspace',
        type: 'boolean',
        ui: 'toggle',
        scopes: ['workspace'],
        default_value: true,
        effective_value: true,
        override_value: null,
        source: 'workspace_override',
        source_explanation: 'Resolved from a workspace override.',
        apply_mode: 'worker_reload',
        activation_state: 'pending_reload',
        active: false,
        pending_value: true,
        affected_process_or_worker: 'live_sessions',
        completion_guidance: 'Reload affected workers to activate this value.',
        options: null,
        constraints: null,
        sensitive: false,
        secret_role: null,
        read_only: false,
        read_only_reason: null,
        requires_reload: false,
        requires_worker_restart: true,
        requires_process_restart: false,
        applies_to: ['live_sessions'],
        depends_on: [],
        order: 20,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 1,
        diagnostics: [{ code: 'restart', message: 'Worker restart required.', severity: 'warning' }],
      },
      {
        key: 'skills.canary_percent',
        title: 'Skills Canary Percent',
        description: 'Percentage of runs routed through skills-first policy.',
        category: 'Workflow',
        section: 'user-workspace',
        type: 'integer',
        ui: 'number',
        scopes: ['workspace'],
        default_value: 100,
        effective_value: 25,
        override_value: null,
        source: 'workspace_override',
        source_explanation: 'Resolved from a workspace override.',
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
        scopes: ['user', 'workspace'],
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
        diagnostics: [],
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
        scopes: ['workspace'],
        default_value: 'comfortable',
        effective_value: 'comfortable',
        override_value: null,
        source: 'default',
        source_explanation: 'Resolved from default.',
        apply_mode: 'immediate',
        activation_state: 'active',
        active: true,
        pending_value: null,
        affected_process_or_worker: 'mission_control',
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
        applies_to: ['mission_control'],
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
        scopes: ['workspace'],
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
        scopes: ['workspace'],
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
        scopes: ['workspace'],
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
        completion_guidance: 'Use the related operation control to activate this value.',
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
    ],
  },
};

const userCatalog = {
  section: 'user-workspace',
  scope: 'user',
  categories: {
    Integrations: [
      {
        ...workspaceCatalog.categories.Integrations[0],
        source: 'workspace_override',
        source_explanation: 'Resolved from a workspace override.',
        effective_value: 'env://WORKSPACE_TOKEN',
        scopes: ['user', 'workspace'],
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

      if (url.includes('scope=user')) {
        return Promise.resolve(jsonResponse(userCatalog));
      }
      if (url.includes('/api/v1/settings/catalog')) {
        return Promise.resolve(jsonResponse(workspaceCatalog));
      }
      if (url === '/api/v1/settings/workspace') {
        return Promise.resolve(jsonResponse({ scope: 'workspace', values: {} }));
      }
      if (url === '/api/v1/settings/workspace/live_sessions.default_enabled') {
        return Promise.resolve(jsonResponse({ key: 'live_sessions.default_enabled' }));
      }
      return Promise.resolve(jsonResponse({ error: 'not_found', message: 'not found' }, false, 404));
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders descriptor rows with metadata, badges, diagnostics, and filters', async () => {
    renderWithClient(<GeneratedSettingsSection />);

    expect(await screen.findByText('Default Publish Mode')).toBeTruthy();
    expect(screen.getByText('Config')).toBeTruthy();
    expect(screen.getAllByText('Workspace')).not.toHaveLength(0);
    expect(screen.getByText('Worker restart')).toBeTruthy();
    expect(screen.getByText('Worker restart required.')).toBeTruthy();
    expect(screen.getAllByText('Applies on next task')).not.toHaveLength(0);
    expect(screen.getAllByText('Pending next boundary')).not.toHaveLength(0);
    expect(screen.getAllByText('New tasks will use this value when they are created.')).not.toHaveLength(0);
    expect(screen.getByText('task_creation')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Search settings'), { target: { value: 'github' } });
    expect(screen.getByText('GitHub Token Reference')).toBeTruthy();
    expect(screen.queryByText('Default Publish Mode')).toBeNull();

    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'Integrations' } });
    expect(screen.getByText('GitHub Token Reference')).toBeTruthy();
  });

  it('switches scope and fetches scoped descriptors', async () => {
    renderWithClient(<GeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    fireEvent.click(screen.getByRole('button', { name: 'User' }));

    await screen.findByDisplayValue('env://WORKSPACE_TOKEN');
    expect(requests.some((request) => request.url.includes('scope=user'))).toBe(true);
    expect(screen.queryByText('Default Publish Mode')).toBeNull();
  });

  it('does not offer reset for inherited workspace overrides in user scope', async () => {
    renderWithClient(<GeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    fireEvent.click(screen.getByRole('button', { name: 'User' }));

    await screen.findByDisplayValue('env://WORKSPACE_TOKEN');
    expect(screen.queryByRole('button', { name: 'Reset GitHub Token Reference' })).toBeNull();
  });

  it('edits generated controls and previews only changed keys', async () => {
    renderWithClient(<GeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');

    fireEvent.change(screen.getByLabelText('Default Publish Mode'), { target: { value: 'branch' } });
    fireEvent.click(screen.getByLabelText('Live Sessions Enabled By Default'));
    fireEvent.change(screen.getByLabelText('Skills Canary Percent'), { target: { value: '50' } });
    fireEvent.change(screen.getByLabelText('UI Density'), { target: { value: 'compact' } });
    fireEvent.change(screen.getByLabelText('Notification Channels'), { target: { value: 'email,slack' } });
    fireEvent.change(screen.getByLabelText('Git Author Defaults'), { target: { value: 'name=MoonMind\\nemail=dev@example.com' } });
    fireEvent.change(screen.getByLabelText('GitHub Token Reference'), { target: { value: 'db://github-main' } });

    const preview = screen.getByLabelText('Pending settings preview');
    expect(within(preview).getByText('workflow.default_publish_mode')).toBeTruthy();
    expect(within(preview).getByText('live_sessions.default_enabled')).toBeTruthy();
    expect(within(preview).getByText('skills.canary_percent')).toBeTruthy();
    expect(within(preview).getByText('ui.density')).toBeTruthy();
    expect(within(preview).getByText('notifications.channels')).toBeTruthy();
    expect(within(preview).getByText('git.author_defaults')).toBeTruthy();
    expect(within(preview).getByText('integrations.github.token_ref')).toBeTruthy();
    expect(within(preview).getAllByText('Valid')).not.toHaveLength(0);
    expect(screen.queryByText(/plaintext/i)).toBeNull();
  });

  it('saves only changed keys with expected versions and refreshes catalog', async () => {
    renderWithClient(<GeneratedSettingsSection />);

    await screen.findByText('Default Publish Mode');
    fireEvent.change(screen.getByLabelText('Default Publish Mode'), { target: { value: 'branch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/settings/workspace',
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({
            changes: { 'workflow.default_publish_mode': 'branch' },
            expected_versions: { 'workflow.default_publish_mode': 3 },
            reason: 'Updated from Mission Control Settings.',
          }),
        }),
      );
    });
  });

  it('disables read-only descriptors and shows lock reason', async () => {
    renderWithClient(<GeneratedSettingsSection />);

    expect(await screen.findByText('Operator Locked Mode')).toBeTruthy();
    expect(screen.getByText('Operator locked by workspace policy.')).toBeTruthy();
    expect((screen.getByLabelText('Operator Locked Mode') as HTMLInputElement).disabled).toBe(true);

    fireEvent.click(screen.getByLabelText('Read-only only'));
    expect(screen.getByText('Operator Locked Mode')).toBeTruthy();
    expect(screen.queryByText('Default Publish Mode')).toBeNull();
  });

  it('resets overrides through the reset route and supports discard', async () => {
    renderWithClient(<GeneratedSettingsSection />);

    await screen.findAllByText('Live Sessions Enabled By Default');
    fireEvent.change(screen.getByLabelText('Default Publish Mode'), { target: { value: 'branch' } });
    expect(screen.getByLabelText('Pending settings preview')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(screen.queryByLabelText('Pending settings preview')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Reset Live Sessions Enabled By Default' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/settings/workspace/live_sessions.default_enabled',
        expect.objectContaining({ method: 'DELETE' }),
      );
    });
  });
});
