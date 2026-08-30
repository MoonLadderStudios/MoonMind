import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BootPayload } from '../boot/parseBootPayload';
import { LoadingPlaceholder } from '../components/dashboard/LoadingPlaceholder';
import { SecretManager } from '../components/secrets/SecretManager';
import { ConfigurationHealthSummary } from '../components/settings/ConfigurationHealthSummary';
import { GeneratedSettingsSection } from '../components/settings/GeneratedSettingsSection';
import { GithubTokenProbePanel } from '../components/settings/GithubTokenProbePanel';
import {
  OperationsSettingsSection,
  type WorkerPauseConfig,
} from '../components/settings/OperationsSettingsSection';
import {
  ALL_RUNTIMES_FILTER_VALUE,
  PROVIDER_PROFILE_QUERY_KEY,
  ProviderProfilesManager,
  type ProviderProfile,
} from '../components/settings/ProviderProfilesManager';
import { resetDashboardPreferences } from '../utils/dashboardPreferences';

// `omnigent` is an execution facade, not a Provider Profile owner: every
// profile it launches is owned by the underlying managed runtime, so it is
// never offered as a Provider Profile runtime filter.
const NON_PROFILE_OWNING_RUNTIMES = new Set(['omnigent']);

interface ProfileData {
  id?: string | number;
  email?: string;
}

interface Notice {
  level: 'ok' | 'error';
  text: string;
}

interface SecretMetadata {
  slug: string;
  status: string;
  details: Record<string, unknown>;
  createdAt: string;
  updatedAt?: string;
}

interface SecretsListResponse {
  items: SecretMetadata[];
}

const SETTINGS_PAGES: ReadonlyArray<{
  id: 'providers-secrets' | 'user-workspace' | 'operations';
  path: string;
  label: string;
  description: string;
}> = [
  {
    id: 'providers-secrets',
    path: '/settings/providers-secrets',
    label: 'Providers & Secrets',
    description:
      'Configure provider profiles, managed secrets, and the bindings that make runtimes launchable.',
  },
  {
    id: 'user-workspace',
    path: '/settings/user-workspace',
    label: 'User / Workspace',
    description:
      'Hold user-scoped and workspace-scoped settings as the dashboard exposes more of the broader configuration model.',
  },
  {
    id: 'operations',
    path: '/settings/operations',
    label: 'Operations',
    description:
      'Keep worker pause, drain, quiesce, and related operational controls under Settings.',
  },
] as const;

function settingsPageForPath(pathname: string): (typeof SETTINGS_PAGES)[number] {
  const normalized = pathname.length > 1 && pathname.endsWith('/')
    ? pathname.slice(0, -1)
    : pathname;
  return SETTINGS_PAGES.find(({ path }) => path === normalized) ?? SETTINGS_PAGES[0]!;
}

export function SettingsPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<Notice | null>(null);
  const currentPage = settingsPageForPath(window.location.pathname);
  const section = currentPage.id;
  // Settings is the administrative exception to runtime-scoped Provider Profile
  // selection: it enters on the All-runtimes view and can narrow the table to a
  // single runtime without narrowing global configuration health.
  const [providerProfileRuntimeFilter, setProviderProfileRuntimeFilter] = useState<string>(
    ALL_RUNTIMES_FILTER_VALUE,
  );
  const workerPauseConfig =
    (payload.initialData as { workerPause?: WorkerPauseConfig } | undefined)?.workerPause ??
    null;
  const runtimeSystemConfig =
    (payload.initialData as {
      runtimeConfig?: {
        system?: {
          defaultTaskModelByRuntime?: Record<string, string>;
          supportedRuntimes?: string[];
        };
      };
    } | undefined)?.runtimeConfig?.system ?? {};
  const defaultTaskModelByRuntime: Record<string, string> =
    runtimeSystemConfig.defaultTaskModelByRuntime ?? {};
  const supportedRuntimes: string[] = runtimeSystemConfig.supportedRuntimes ?? [];
  const settingsPermissions = new Set(
    ((payload.initialData as { settingsPermissions?: string[] } | undefined)
      ?.settingsPermissions ?? []),
  );
  const canWriteProviderProfiles = settingsPermissions.has('provider_profiles.write');
  const canRunGithubTokenProbe = settingsPermissions.has('settings.effective.read');

  useEffect(() => {
    const previousTitle = document.title;
    document.title = `${currentPage.label} | MoonMind`;
    return () => {
      document.title = previousTitle;
    };
  }, [currentPage.label]);

  const { data: profile, isLoading, isError } = useQuery<ProfileData>({
    queryKey: ['profile'],
    queryFn: async () => {
      const response = await fetch('/me', {
        credentials: 'include',
        headers: {
          Accept: 'application/json',
        },
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch profile: ${response.statusText}`);
      }
      return response.json();
    },
    enabled: section === 'user-workspace',
  });

  const {
    data: secretsData,
    isLoading: areSecretsLoading,
    isError: areSecretsErrored,
  } = useQuery<SecretsListResponse>({
    queryKey: ['secrets'],
    queryFn: async () => {
      const response = await fetch('/api/v1/secrets', {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch secrets: ${response.statusText}`);
      }
      return response.json();
    },
  });

  const {
    data: providerProfiles,
    isLoading: areProfilesLoading,
    isError: areProfilesErrored,
  } = useQuery<ProviderProfile[]>({
    queryKey: PROVIDER_PROFILE_QUERY_KEY,
    queryFn: async () => {
      const response = await fetch('/api/v1/provider-profiles', {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch provider profiles: ${response.statusText}`);
      }
      return response.json();
    },
  });

  // The complete collection stays the single source for configuration health so
  // global counts and diagnostics never follow the table filter.
  const allProviderProfiles = providerProfiles ?? [];
  const providerProfileRuntimeOptions = useMemo(() => {
    const runtimeIds: string[] = [];
    for (const runtimeId of [
      ...supportedRuntimes,
      ...allProviderProfiles.map((profile) => profile.runtime_id ?? ''),
    ]) {
      const canonical = String(runtimeId ?? '').trim();
      if (
        canonical &&
        !NON_PROFILE_OWNING_RUNTIMES.has(canonical) &&
        !runtimeIds.includes(canonical)
      ) {
        runtimeIds.push(canonical);
      }
    }
    return runtimeIds;
  }, [supportedRuntimes, allProviderProfiles]);
  const visibleProviderProfiles =
    providerProfileRuntimeFilter === ALL_RUNTIMES_FILTER_VALUE
      ? allProviderProfiles
      : allProviderProfiles.filter(
          (profile) => profile.runtime_id === providerProfileRuntimeFilter,
        );

  return (
    <div className="settings-page mx-auto w-full space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="rounded-[2rem] border border-mm-border/80 bg-transparent px-6 py-6 shadow-sm">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
            Dashboard Settings
          </p>
          <h2 className="text-3xl font-semibold tracking-tight text-slate-950 dark:text-white">{currentPage.label}</h2>
          <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">{currentPage.description}</p>
        </div>
      </header>

      <ConfigurationHealthSummary
        providerProfiles={allProviderProfiles}
        secrets={secretsData?.items ?? []}
        isLoading={areProfilesLoading || areSecretsLoading}
        isError={areProfilesErrored || areSecretsErrored}
        workerPauseConfig={workerPauseConfig}
        canWriteProviderProfiles={canWriteProviderProfiles}
        canRunGithubTokenProbe={canRunGithubTokenProbe}
      />

      {notice ? (
        <div
          className={`rounded-3xl border px-5 py-4 text-sm shadow-sm ${
            notice.level === 'error'
              ? 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400'
              : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-400'
          }`}
        >
          {notice.text}
        </div>
      ) : null}

      {section === 'providers-secrets' ? (
        <div className="space-y-6">
          <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
              <div className="space-y-2">
                <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Provider profile and secret management</h3>
                <p className="text-sm text-slate-600 dark:text-slate-400">
                  Provider profiles are the durable runtime and provider launch contract.
                  Managed secrets back those profiles without re-exposing raw credential
                  values after creation.
                </p>
              </div>
              <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/50 p-4 text-sm text-slate-600 dark:text-slate-400">
                Use secret refs such as <code>db://OPENAI_API_KEY</code> inside provider
                profiles. Secrets stay in the managed secret store; profiles only keep the
                refs and launch metadata.
              </div>
            </div>
          </section>

          {areProfilesLoading ? (
            <LoadingPlaceholder
              surface="settings"
              region="provider profiles"
              variant="table"
              density="compact"
              preserveContext
            />
          ) : areProfilesErrored ? (
            <div className="rounded-3xl border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-900/20 p-6 text-sm text-rose-700 dark:text-rose-400 shadow-sm">
              Failed to load provider profiles.
            </div>
          ) : (
            <ProviderProfilesManager
              profiles={visibleProviderProfiles}
              secretSlugs={(secretsData?.items ?? []).map((secret) => secret.slug)}
              onNotice={setNotice}
              queryClient={queryClient}
              defaultTaskModelByRuntime={defaultTaskModelByRuntime}
              canWriteProviderProfiles={canWriteProviderProfiles}
              selectedRuntimeId={
                providerProfileRuntimeFilter === ALL_RUNTIMES_FILTER_VALUE
                  ? undefined
                  : providerProfileRuntimeFilter
              }
              runtimeFilterOptions={providerProfileRuntimeOptions}
              onSelectRuntimeId={(runtimeId) =>
                setProviderProfileRuntimeFilter(runtimeId ?? ALL_RUNTIMES_FILTER_VALUE)
              }
            />
          )}

          {areSecretsLoading ? (
            <LoadingPlaceholder
              surface="settings"
              region="managed secrets"
              variant="table"
              density="compact"
              preserveContext
            />
          ) : areSecretsErrored ? (
            <div className="rounded-3xl border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-900/20 p-6 text-sm text-rose-700 dark:text-rose-400 shadow-sm">
              Failed to load managed secrets.
            </div>
          ) : (
            <SecretManager
              secrets={secretsData?.items ?? []}
              onNotice={setNotice}
              queryClient={queryClient}
              permissions={settingsPermissions}
            />
          )}

          <GithubTokenProbePanel
            canRunProbe={canRunGithubTokenProbe}
            onNotice={setNotice}
          />
        </div>
      ) : null}

      {section === 'user-workspace' ? (
        <div className="space-y-6">
          <GeneratedSettingsSection />

          <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
            <h3 className="text-base font-semibold text-slate-900 dark:text-white">
              Dashboard preferences
            </h3>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
              Clear saved list layouts, selected workflows and recurring schedules, and other
              browser-local dashboard choices.
            </p>
            <button
              type="button"
              className="mt-4 rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mm-accent dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
              onClick={() => {
                resetDashboardPreferences();
                setNotice({ level: 'ok', text: 'Dashboard preferences reset.' });
              }}
            >
              Reset dashboard preferences
            </button>
          </section>

          <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
            {isLoading ? (
              <LoadingPlaceholder
                surface="settings"
                region="current user"
                variant="settings"
                density="normal"
                preserveContext
              />
            ) : isError ? (
              <p className="text-sm text-rose-700 dark:text-rose-400">Failed to load profile data.</p>
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/50 p-5">
                  <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Signed-in user</div>
                  <div className="mt-2 text-base font-semibold text-slate-900 dark:text-white">
                    {profile?.email || 'Unknown user'}
                  </div>
                  {profile?.id ? (
                    <div className="mt-1 font-mono text-xs text-slate-500 dark:text-slate-400">
                      {profile.id}
                    </div>
                  ) : null}
                </div>
                <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/50 p-5 text-sm text-slate-600 dark:text-slate-400">
                  Future user and workspace settings should land here instead of adding
                  more top-level tabs. This keeps the main product surface centered on
                  tasks while still leaving room for the project&apos;s wider configuration
                  model.
                </div>
              </div>
            )}
          </section>
        </div>
      ) : null}

      {section === 'operations' ? (
        <OperationsSettingsSection workerPauseConfig={workerPauseConfig} />
      ) : null}
    </div>
  );
}
export default SettingsPage;
