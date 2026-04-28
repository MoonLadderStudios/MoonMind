import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

type SettingScope = 'workspace' | 'user';

interface SettingOption {
  value: string;
  label: string;
}

interface SettingConstraints {
  minimum?: number | null;
  maximum?: number | null;
  min_length?: number | null;
  max_length?: number | null;
  pattern?: string | null;
}

interface SettingDiagnostic {
  code: string;
  message: string;
  severity: 'info' | 'warning' | 'error';
}

interface SettingDescriptor {
  key: string;
  title: string;
  description?: string | null;
  category: string;
  section: string;
  type: string;
  ui: string;
  scopes: SettingScope[];
  default_value?: unknown;
  effective_value: unknown;
  override_value?: unknown;
  source: string;
  source_explanation: string;
  apply_mode: string;
  activation_state: string;
  active: boolean;
  pending_value?: unknown;
  affected_process_or_worker?: string | null;
  completion_guidance?: string | null;
  options?: SettingOption[] | null;
  constraints?: SettingConstraints | null;
  sensitive: boolean;
  secret_role?: string | null;
  read_only: boolean;
  read_only_reason?: string | null;
  requires_reload: boolean;
  requires_worker_restart: boolean;
  requires_process_restart: boolean;
  applies_to: string[];
  order: number;
  value_version: number;
  diagnostics: SettingDiagnostic[];
}

interface SettingsCatalogResponse {
  section: string;
  scope: SettingScope;
  categories: Record<string, SettingDescriptor[]>;
}

interface PendingChange {
  descriptor: SettingDescriptor;
  value: unknown;
  valid: boolean;
  message?: string;
}

const SCOPE_LABELS: Record<SettingScope, string> = {
  workspace: 'Workspace',
  user: 'User',
};

const SOURCE_LABELS: Record<string, string> = {
  config_or_default: 'Config',
  default: 'Default',
  environment: 'Environment',
  workspace_override: 'Workspace override',
  user_override: 'User override',
  operator_locked: 'Operator locked',
  provider_profile: 'Provider profile',
  secret_reference: 'Secret reference',
};

const APPLY_MODE_LABELS: Record<string, string> = {
  immediate: 'Applies immediately',
  next_request: 'Applies on next request',
  next_task: 'Applies on next task',
  next_launch: 'Applies on next launch',
  worker_reload: 'Requires worker reload',
  process_restart: 'Requires process restart',
  manual_operation: 'Requires manual operation',
};

const ACTIVATION_STATE_LABELS: Record<string, string> = {
  active: 'Active',
  pending_next_boundary: 'Pending next boundary',
  pending_reload: 'Pending reload',
  pending_restart: 'Pending restart',
  pending_manual_operation: 'Pending manual operation',
};

const SETTING_FIELD_CLASS_NAMES = [
  'w-full',
  'rounded-xl',
  'border',
  'border-slate-300',
  'bg-white',
  'px-3',
  'py-2',
  'text-sm',
  'text-slate-900',
  'disabled:bg-slate-100',
  'disabled:text-slate-500',
  'dark:border-slate-700',
  'dark:bg-slate-950',
  'dark:text-white',
  'dark:disabled:bg-slate-900',
  'dark:disabled:text-slate-500',
];

function settingFieldClassName(...classNames: string[]): string {
  return [...SETTING_FIELD_CLASS_NAMES, ...classNames].filter(Boolean).join(' ');
}

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source.replaceAll('_', ' ');
}

function applyModeLabel(applyMode: string): string {
  return APPLY_MODE_LABELS[applyMode] ?? applyMode.replaceAll('_', ' ');
}

function activationStateLabel(activationState: string): string {
  return ACTIVATION_STATE_LABELS[activationState] ?? activationState.replaceAll('_', ' ');
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) {
    return 'Not set';
  }
  if (typeof value === 'boolean') {
    return value ? 'Enabled' : 'Disabled';
  }
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number') {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.join(', ');
  }
  return JSON.stringify(value);
}

function inputValue(value: unknown, descriptor: SettingDescriptor): string {
  if (Array.isArray(value)) {
    return value.join(',');
  }
  if (descriptor.ui === 'key_value' || descriptor.type === 'object') {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return Object.entries(value as Record<string, unknown>)
        .map(([key, nestedValue]) => `${key}=${String(nestedValue ?? '')}`)
        .join('\n');
    }
    return '';
  }
  if (value === null || value === undefined) {
    return '';
  }
  return String(value);
}

function parseList(raw: string): string[] {
  return raw
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseKeyValue(raw: string): Record<string, string> {
  const parsed: Record<string, string> = {};
  raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line) => {
      const separator = line.indexOf('=');
      if (separator === -1) {
        parsed[line] = '';
        return;
      }
      parsed[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
    });
  return parsed;
}

function enumerableKeys(value: object): Array<string | symbol> {
  return Reflect.ownKeys(value)
    .filter((key) => Object.prototype.propertyIsEnumerable.call(value, key))
    .sort((left, right) => String(left).localeCompare(String(right)));
}

function valuesEqual(left: unknown, right: unknown, seen = new WeakMap<object, WeakSet<object>>()): boolean {
  if (Object.is(left, right)) {
    return true;
  }
  if (typeof left !== 'object' || left === null || typeof right !== 'object' || right === null) {
    return false;
  }
  const seenRights = seen.get(left);
  if (seenRights?.has(right)) {
    return true;
  }
  if (seenRights) {
    seenRights.add(right);
  } else {
    seen.set(left, new WeakSet([right]));
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) => valuesEqual(item, right[index], seen))
    );
  }
  const leftKeys = enumerableKeys(left);
  const rightKeys = enumerableKeys(right);
  const leftRecord = left as Record<string | symbol, unknown>;
  const rightRecord = right as Record<string | symbol, unknown>;
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] &&
        valuesEqual(leftRecord[key], rightRecord[key], seen),
    )
  );
}

function coerceInputValue(descriptor: SettingDescriptor, raw: string | boolean): PendingChange {
  let value: unknown;
  let valid = true;
  let message: string | undefined;

  if (descriptor.type === 'boolean' || descriptor.ui === 'toggle') {
    value = Boolean(raw);
  } else if (descriptor.type === 'integer' || descriptor.type === 'number' || descriptor.ui === 'number') {
    const numeric = Number(raw);
    value = Number.isInteger(numeric) ? numeric : Number(raw);
    if (raw === '' || Number.isNaN(numeric)) {
      valid = false;
      message = 'Enter a number.';
    } else if (descriptor.constraints?.minimum !== null && descriptor.constraints?.minimum !== undefined && numeric < descriptor.constraints.minimum) {
      valid = false;
      message = `Minimum is ${descriptor.constraints.minimum}.`;
    } else if (descriptor.constraints?.maximum !== null && descriptor.constraints?.maximum !== undefined && numeric > descriptor.constraints.maximum) {
      valid = false;
      message = `Maximum is ${descriptor.constraints.maximum}.`;
    }
  } else if (descriptor.type === 'list' || descriptor.ui === 'tag_editor') {
    value = parseList(String(raw));
  } else if (descriptor.type === 'object' || descriptor.ui === 'key_value') {
    value = parseKeyValue(String(raw));
  } else {
    value = String(raw);
    if ((descriptor.type === 'secret_ref' || descriptor.ui === 'secret_ref_picker') && value && !String(value).includes('://')) {
      valid = false;
      message = 'Enter a SecretRef such as db://name or env://NAME.';
    }
  }

  return message === undefined
    ? { descriptor, value, valid }
    : { descriptor, value, valid, message };
}

function reloadBadges(descriptor: SettingDescriptor): string[] {
  const badges: string[] = [];
  if (descriptor.requires_reload) {
    badges.push('Reload');
  }
  if (descriptor.requires_worker_restart) {
    badges.push('Worker restart');
  }
  if (descriptor.requires_process_restart) {
    badges.push('Process restart');
  }
  return badges;
}

function canReset(descriptor: SettingDescriptor, scope: SettingScope): boolean {
  return (
    (scope === 'workspace' && descriptor.source === 'workspace_override') ||
    (scope === 'user' && descriptor.source === 'user_override')
  );
}

async function fetchCatalog(scope: SettingScope): Promise<SettingsCatalogResponse> {
  const response = await fetch(`/api/v1/settings/catalog?section=user-workspace&scope=${scope}`, {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Failed to fetch settings catalog: ${response.statusText}`);
  }
  return response.json();
}

function flattenedDescriptors(catalog?: SettingsCatalogResponse): SettingDescriptor[] {
  if (!catalog) {
    return [];
  }
  return Object.values(catalog.categories)
    .flat()
    .sort((left, right) => left.order - right.order);
}

function sanitizeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return 'Settings request failed.';
}

export function GeneratedSettingsSection() {
  const queryClient = useQueryClient();
  const [scope, setScope] = useState<SettingScope>('workspace');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [modifiedOnly, setModifiedOnly] = useState(false);
  const [readOnlyOnly, setReadOnlyOnly] = useState(false);
  const [pending, setPending] = useState<Record<string, PendingChange>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const catalogQuery = useQuery({
    queryKey: ['settings-catalog', scope],
    queryFn: () => fetchCatalog(scope),
  });

  const descriptors = useMemo(() => flattenedDescriptors(catalogQuery.data), [catalogQuery.data]);
  const categories = useMemo(
    () => Array.from(new Set(descriptors.map((descriptor) => descriptor.category))).sort(),
    [descriptors],
  );

  const visibleDescriptors = descriptors.filter((descriptor) => {
    const haystack = `${descriptor.key} ${descriptor.title} ${descriptor.description ?? ''}`.toLowerCase();
    if (search.trim() && !haystack.includes(search.trim().toLowerCase())) {
      return false;
    }
    if (category !== 'all' && descriptor.category !== category) {
      return false;
    }
    if (modifiedOnly && !pending[descriptor.key]) {
      return false;
    }
    if (readOnlyOnly && !descriptor.read_only) {
      return false;
    }
    return true;
  });

  const visibleByCategory = visibleDescriptors.reduce<Record<string, SettingDescriptor[]>>((grouped, descriptor) => {
    const categoryDescriptors = grouped[descriptor.category] ?? [];
    categoryDescriptors.push(descriptor);
    grouped[descriptor.category] = categoryDescriptors;
    return grouped;
  }, {});

  const pendingChanges = Object.values(pending);
  const hasInvalidChange = pendingChanges.some((change) => !change.valid);

  const updatePending = (descriptor: SettingDescriptor, raw: string | boolean) => {
    const next = coerceInputValue(descriptor, raw);
    setPending((current) => {
      const updated = { ...current };
      if (valuesEqual(next.value, descriptor.effective_value)) {
        delete updated[descriptor.key];
      } else {
        updated[descriptor.key] = next;
      }
      return updated;
    });
  };

  const resetLocalStateForScope = (nextScope: SettingScope) => {
    setScope(nextScope);
    setPending({});
    setNotice(null);
    setCategory('all');
    setSearch('');
    setModifiedOnly(false);
    setReadOnlyOnly(false);
  };

  const saveChanges = async () => {
    if (!pendingChanges.length || hasInvalidChange) {
      return;
    }
    const changes = Object.fromEntries(pendingChanges.map((change) => [change.descriptor.key, change.value]));
    const expected_versions = Object.fromEntries(
      pendingChanges.map((change) => [change.descriptor.key, change.descriptor.value_version]),
    );
    try {
      const response = await fetch(`/api/v1/settings/${scope}`, {
        method: 'PATCH',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          changes,
          expected_versions,
          reason: 'Updated from Mission Control Settings.',
        }),
      });
      if (!response.ok) {
        throw new Error(`Settings save failed with status ${response.status}.`);
      }
      setPending({});
      setNotice('Settings saved.');
      await queryClient.invalidateQueries({ queryKey: ['settings-catalog', scope] });
    } catch (error) {
      setNotice(sanitizeError(error));
    }
  };

  const resetOverride = async (descriptor: SettingDescriptor) => {
    try {
      const response = await fetch(`/api/v1/settings/${scope}/${encodeURIComponent(descriptor.key)}`, {
        method: 'DELETE',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Reset failed with status ${response.status}.`);
      }
      setPending((current) => {
        const updated = { ...current };
        delete updated[descriptor.key];
        return updated;
      });
      setNotice(`${descriptor.title} reset.`);
      await queryClient.invalidateQueries({ queryKey: ['settings-catalog', scope] });
    } catch (error) {
      setNotice(sanitizeError(error));
    }
  };

  if (catalogQuery.isLoading) {
    return (
      <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 text-sm text-slate-500 shadow-sm dark:text-slate-400">
        Loading generated settings...
      </section>
    );
  }

  if (catalogQuery.isError) {
    return (
      <section className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700 shadow-sm dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400">
        {sanitizeError(catalogQuery.error)}
      </section>
    );
  }

  return (
    <section className="space-y-5" aria-label="Generated user and workspace settings">
      <div className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-2">
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white">User / Workspace</h3>
            <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">
              Configure eligible settings from backend-owned descriptors. Validation, sensitivity, and authorization remain server-side.
            </p>
          </div>
          <div className="flex rounded-full border border-slate-300 p-1 dark:border-slate-700" aria-label="Settings scope">
            {(['workspace', 'user'] as SettingScope[]).map((candidate) => (
              <button
                key={candidate}
                type="button"
                className={`rounded-full px-4 py-2 text-sm font-medium ${
                  scope === candidate
                    ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900'
                    : 'text-slate-600 hover:text-slate-950 dark:text-slate-400 dark:hover:text-white'
                }`}
                onClick={() => resetLocalStateForScope(candidate)}
              >
                {SCOPE_LABELS[candidate]}
              </button>
            ))}
          </div>
        </div>
      </div>

      {notice ? (
        <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 shadow-sm dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-300">
          {notice}
        </div>
      ) : null}

      <div className="rounded-3xl border border-mm-border/80 bg-transparent p-5 shadow-sm">
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px_auto_auto] md:items-end">
          <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Search settings
            <input
              className="mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Category
            <select
              className="mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              <option value="all">All categories</option>
              {categories.map((candidate) => (
                <option key={candidate} value={candidate}>
                  {candidate}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-300">
            <input type="checkbox" checked={modifiedOnly} onChange={(event) => setModifiedOnly(event.target.checked)} />
            Modified only
          </label>
          <label className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-300">
            <input type="checkbox" checked={readOnlyOnly} onChange={(event) => setReadOnlyOnly(event.target.checked)} />
            Read-only only
          </label>
        </div>
      </div>

      {pendingChanges.length ? (
        <div
          className="rounded-3xl border border-amber-200 bg-amber-50 p-5 shadow-sm dark:border-amber-900/60 dark:bg-amber-950/30"
          aria-label="Pending settings preview"
        >
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <h4 className="text-sm font-semibold text-amber-950 dark:text-amber-100">Change preview</h4>
              <p className="text-sm text-amber-800 dark:text-amber-200">Review changed keys before saving.</p>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                className="rounded-xl border border-amber-300 px-3 py-2 text-sm font-medium text-amber-900 hover:bg-amber-100 dark:border-amber-800 dark:text-amber-100 dark:hover:bg-amber-900/40"
                onClick={() => setPending({})}
              >
                Discard changes
              </button>
              <button
                type="button"
                className="rounded-xl bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
                disabled={hasInvalidChange}
                onClick={saveChanges}
              >
                Save changes
              </button>
            </div>
          </div>
          <div className="mt-4 grid gap-3">
            {pendingChanges.map((change) => (
              <div key={change.descriptor.key} className="rounded-2xl border border-amber-200 bg-white/70 p-3 text-sm dark:border-amber-900/60 dark:bg-slate-950/50">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs font-semibold text-slate-900 dark:text-white">{change.descriptor.key}</span>
                  <span className={change.valid ? 'text-emerald-700 dark:text-emerald-300' : 'text-rose-700 dark:text-rose-300'}>
                    {change.valid ? 'Valid' : change.message ?? 'Invalid'}
                  </span>
                </div>
                <div className="mt-2 grid gap-2 text-slate-700 dark:text-slate-300 md:grid-cols-2">
                  <div>Old: {displayValue(change.descriptor.effective_value)}</div>
                  <div>New: {displayValue(change.value)}</div>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {change.descriptor.applies_to.map((target) => (
                    <span key={target} className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                      {target}
                    </span>
                  ))}
                  {reloadBadges(change.descriptor).map((badge) => (
                    <span key={badge} className="rounded-full bg-amber-100 px-2 py-1 text-xs text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
                      {badge}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {visibleDescriptors.length === 0 ? (
        <div className="rounded-3xl border border-mm-border/80 bg-transparent p-6 text-sm text-slate-500 shadow-sm dark:text-slate-400">
          No settings match the current filters.
        </div>
      ) : null}

      {Object.entries(visibleByCategory).map(([categoryName, categoryDescriptors]) => (
        <div key={categoryName} className="rounded-3xl border border-mm-border/80 bg-transparent p-5 shadow-sm">
          <h4 className="text-base font-semibold text-slate-900 dark:text-white">{categoryName}</h4>
          <div className="mt-4 divide-y divide-slate-200 dark:divide-slate-800">
            {categoryDescriptors.map((descriptor) => {
              const pendingChange = pending[descriptor.key];
              const currentValue = pendingChange?.value ?? descriptor.effective_value;
              return (
                <div key={descriptor.key} className="grid gap-4 py-5 lg:grid-cols-[minmax(0,1fr)_minmax(280px,420px)]">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <h5 className="text-sm font-semibold text-slate-950 dark:text-white">{descriptor.title}</h5>
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                        {sourceLabel(descriptor.source)}
                      </span>
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                        {SCOPE_LABELS[scope]}
                      </span>
                      <span className="rounded-full bg-sky-100 px-2 py-1 text-xs font-medium text-sky-800 dark:bg-sky-900/40 dark:text-sky-200">
                        {applyModeLabel(descriptor.apply_mode)}
                      </span>
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                        {activationStateLabel(descriptor.activation_state)}
                      </span>
                      {reloadBadges(descriptor).map((badge) => (
                        <span key={badge} className="rounded-full bg-amber-100 px-2 py-1 text-xs font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
                          {badge}
                        </span>
                      ))}
                    </div>
                    {descriptor.description ? (
                      <p className="text-sm text-slate-600 dark:text-slate-400">{descriptor.description}</p>
                    ) : null}
                    <p className="text-xs text-slate-500 dark:text-slate-500">{descriptor.source_explanation}</p>
                    {descriptor.affected_process_or_worker ? (
                      <p className="text-xs text-slate-500 dark:text-slate-500">
                        Affects {descriptor.affected_process_or_worker}
                      </p>
                    ) : null}
                    {!descriptor.active && descriptor.pending_value !== null && descriptor.pending_value !== undefined ? (
                      <p className="text-xs text-slate-500 dark:text-slate-500">
                        Pending: {displayValue(descriptor.pending_value)}
                      </p>
                    ) : null}
                    {descriptor.completion_guidance ? (
                      <p className="text-sm text-slate-600 dark:text-slate-400">{descriptor.completion_guidance}</p>
                    ) : null}
                    {descriptor.read_only && descriptor.read_only_reason ? (
                      <p className="text-sm font-medium text-amber-700 dark:text-amber-300">{descriptor.read_only_reason}</p>
                    ) : null}
                    {descriptor.diagnostics.map((diagnostic) => (
                      <p key={`${descriptor.key}-${diagnostic.code}`} className="text-sm text-amber-700 dark:text-amber-300">
                        {diagnostic.message}
                      </p>
                    ))}
                    <div className="flex flex-wrap gap-2">
                      {descriptor.applies_to.map((target) => (
                        <span key={target} className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                          {target}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="space-y-3">
                    {renderControl(descriptor, currentValue, updatePending)}
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-xs text-slate-500 dark:text-slate-500">{descriptor.key}</span>
                      {canReset(descriptor, scope) ? (
                        <button
                          type="button"
                          className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:border-slate-400 hover:text-slate-950 dark:border-slate-700 dark:text-slate-300 dark:hover:border-slate-500 dark:hover:text-white"
                          onClick={() => resetOverride(descriptor)}
                          aria-label={`Reset ${descriptor.title}`}
                        >
                          Reset
                        </button>
                      ) : null}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </section>
  );
}

function renderControl(
  descriptor: SettingDescriptor,
  value: unknown,
  onChange: (descriptor: SettingDescriptor, value: string | boolean) => void,
) {
  const disabled = descriptor.read_only;
  const commonClassName = settingFieldClassName();

  if (descriptor.type === 'boolean' || descriptor.ui === 'toggle') {
    return (
      <label className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 p-3 text-sm font-medium text-slate-700 dark:border-slate-800 dark:text-slate-300">
        <span>{descriptor.title}</span>
        <input
          aria-label={descriptor.title}
          type="checkbox"
          checked={Boolean(value)}
          disabled={disabled}
          onChange={(event) => onChange(descriptor, event.target.checked)}
        />
      </label>
    );
  }

  if (descriptor.type === 'enum' || descriptor.ui === 'select') {
    return (
      <select
        aria-label={descriptor.title}
        className={commonClassName}
        value={inputValue(value, descriptor)}
        disabled={disabled}
        onChange={(event) => onChange(descriptor, event.target.value)}
      >
        {(descriptor.options ?? []).map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  if (descriptor.type === 'integer' || descriptor.type === 'number' || descriptor.ui === 'number') {
    return (
      <input
        aria-label={descriptor.title}
        className={commonClassName}
        type="number"
        min={descriptor.constraints?.minimum ?? undefined}
        max={descriptor.constraints?.maximum ?? undefined}
        value={inputValue(value, descriptor)}
        disabled={disabled}
        onChange={(event) => onChange(descriptor, event.target.value)}
      />
    );
  }

  if (descriptor.type === 'list' || descriptor.ui === 'tag_editor') {
    return (
      <input
        aria-label={descriptor.title}
        className={commonClassName}
        type="text"
        value={inputValue(value, descriptor)}
        disabled={disabled}
        placeholder="value-a,value-b"
        onChange={(event) => onChange(descriptor, event.target.value)}
      />
    );
  }

  if (descriptor.type === 'object' || descriptor.ui === 'key_value') {
    return (
      <textarea
        aria-label={descriptor.title}
        className={commonClassName}
        rows={3}
        value={inputValue(value, descriptor)}
        disabled={disabled}
        placeholder="key=value"
        onChange={(event) => onChange(descriptor, event.target.value)}
      />
    );
  }

  if (descriptor.read_only || descriptor.ui === 'readonly') {
    return (
      <input
        aria-label={descriptor.title}
        className={commonClassName}
        type="text"
        value={displayValue(value)}
        disabled
        readOnly
      />
    );
  }

  return (
    <input
      aria-label={descriptor.title}
      className={commonClassName}
      type="text"
      value={inputValue(value, descriptor)}
      disabled={disabled}
      placeholder={descriptor.type === 'secret_ref' || descriptor.ui === 'secret_ref_picker' ? 'db://secret-name or env://NAME' : undefined}
      onChange={(event) => onChange(descriptor, event.target.value)}
    />
  );
}
