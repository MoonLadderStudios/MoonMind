import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate, useSearchParams } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { LoadingPlaceholder } from '../components/dashboard/LoadingPlaceholder';
import { SecretManager } from '../components/secrets/SecretManager';
import { ConfigurationHealthSummary } from '../components/settings/ConfigurationHealthSummary';
import {
  GeneratedSettingsSection,
  type SettingScope,
} from '../components/settings/GeneratedSettingsSection';
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
import {
  SettingsDraftGuardProvider,
  useSettingsDraftGuard,
} from '../components/settings/SettingsDraftGuard';
import { resetDashboardPreferences } from '../utils/dashboardPreferences';

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

interface SettingsInitialData {
  settingsPermissions?: string[];
  workerPause?: WorkerPauseConfig | null;
  runtimeConfig?: {
    system?: {
      defaultTaskModelByRuntime?: Record<string, string>;
      supportedRuntimes?: string[];
    };
  };
}

function settingsInitialData(payload: BootPayload): SettingsInitialData {
  return (payload.initialData as SettingsInitialData | undefined) ?? {};
}

function settingsPermissions(payload: BootPayload): Set<string> {
  return new Set(settingsInitialData(payload).settingsPermissions ?? []);
}

function SettingsPageFrame({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  useEffect(() => {
    document.title = `${title} | MoonMind`;
  }, [title]);

  return (
    <div className="settings-page mx-auto w-full space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="rounded-[2rem] border border-mm-border/80 bg-transparent px-6 py-6 shadow-sm">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
            Configuration
          </p>
          <h2 className="text-3xl font-semibold tracking-tight text-slate-950 dark:text-white">
            {title}
          </h2>
          <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            {description}
          </p>
        </div>
      </header>
      {children}
    </div>
  );
}

function SettingsUnavailableState({
  title,
  permissions,
}: {
  title: string;
  permissions: string[];
}) {
  return (
    <section
      aria-label={`${title} unavailable`}
      className="rounded-3xl border border-amber-200 bg-amber-50 p-6 shadow-sm dark:border-amber-900/50 dark:bg-amber-900/20"
    >
      <h3 className="text-lg font-semibold text-amber-950 dark:text-amber-100">
        This configuration page is unavailable
      </h3>
      <p className="mt-2 text-sm text-amber-800 dark:text-amber-200">
        Your account cannot inspect this destination. Direct navigation remains on this route so
        the authorization boundary is explicit.
      </p>
      <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
        Required inspection permission: {permissions.map((permission) => (
          <code key={permission} className="ml-1">{permission}</code>
        ))}
      </p>
    </section>
  );
}

function RegionUnavailable({ children }: { children: ReactNode }) {
  return (
    <section className="rounded-3xl border border-slate-200 bg-slate-50 p-6 text-sm text-slate-600 shadow-sm dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">
      {children}
    </section>
  );
}

function NoticeBanner({ notice }: { notice: Notice | null }) {
  if (!notice) return null;
  return (
    <div
      className={`rounded-3xl border px-5 py-4 text-sm shadow-sm ${
        notice.level === 'error'
          ? 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400'
          : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-400'
      }`}
    >
      {notice.text}
    </div>
  );
}

function ProvidersSecretsSettingsContent({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const { requestDeparture } = useSettingsDraftGuard();
  const [searchParams, setSearchParams] = useSearchParams();
  const [notice, setNotice] = useState<Notice | null>(null);
  const permissions = settingsPermissions(payload);
  const canReadProviderProfiles = permissions.has('provider_profiles.read');
  const canWriteProviderProfiles = permissions.has('provider_profiles.write');
  const canReadSecretMetadata = permissions.has('secrets.metadata.read');
  const canRunGithubTokenProbe = permissions.has('settings.effective.read');
  const runtimeSystemConfig = settingsInitialData(payload).runtimeConfig?.system ?? {};
  const defaultTaskModelByRuntime = runtimeSystemConfig.defaultTaskModelByRuntime ?? {};
  const supportedRuntimes = runtimeSystemConfig.supportedRuntimes ?? [];
  const providerProfileRuntimeFilter =
    searchParams.get('runtime')?.trim() || ALL_RUNTIMES_FILTER_VALUE;

  const secretsQuery = useQuery<SecretsListResponse>({
    queryKey: ['secrets'],
    queryFn: async () => {
      const response = await fetch('/api/v1/secrets', {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`Failed to fetch secrets: ${response.statusText}`);
      return response.json();
    },
    enabled: canReadSecretMetadata,
  });

  const providerProfilesQuery = useQuery<ProviderProfile[]>({
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
    enabled: canReadProviderProfiles,
  });

  const allProviderProfiles = providerProfilesQuery.data ?? [];
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
  }, [allProviderProfiles, supportedRuntimes]);
  const visibleProviderProfiles =
    providerProfileRuntimeFilter === ALL_RUNTIMES_FILTER_VALUE
      ? allProviderProfiles
      : allProviderProfiles.filter(
          (profile) => profile.runtime_id === providerProfileRuntimeFilter,
        );

  const selectRuntime = (runtimeId: string | undefined) => {
    const nextRuntime = runtimeId ?? ALL_RUNTIMES_FILTER_VALUE;
    if (nextRuntime === providerProfileRuntimeFilter) return;
    requestDeparture(() => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        if (nextRuntime === ALL_RUNTIMES_FILTER_VALUE) next.delete('runtime');
        else next.set('runtime', nextRuntime);
        return next;
      });
      setNotice(null);
    }, 'Change the runtime filter? Your unsaved Provider Profile draft will be discarded.');
  };

  return (
    <SettingsPageFrame
      title="Providers & Secrets"
      description="Manage launch-ready Provider Profiles, credential references, OAuth lifecycle, and managed secret metadata."
    >
      {canReadProviderProfiles && canReadSecretMetadata ? (
        <ConfigurationHealthSummary
          providerProfiles={allProviderProfiles}
          secrets={secretsQuery.data?.items ?? []}
          isLoading={providerProfilesQuery.isLoading || secretsQuery.isLoading}
          isError={providerProfilesQuery.isError || secretsQuery.isError}
          canWriteProviderProfiles={canWriteProviderProfiles}
          canRunGithubTokenProbe={canRunGithubTokenProbe}
        />
      ) : (
        <RegionUnavailable>
          The complete launch-readiness summary requires both <code>provider_profiles.read</code>{' '}
          and <code>secrets.metadata.read</code>. Accessible regions remain available below.
        </RegionUnavailable>
      )}

      <NoticeBanner notice={notice} />

      <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Provider Profiles keep launch metadata and SecretRefs. Managed Secrets keep encrypted
          values or external references; profiles never expose those values after creation.
        </p>
      </section>

      {canReadProviderProfiles ? (
        providerProfilesQuery.isLoading ? (
          <LoadingPlaceholder surface="settings" region="provider profiles" variant="table" density="compact" preserveContext />
        ) : providerProfilesQuery.isError ? (
          <div className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700 shadow-sm dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400">
            Failed to load provider profiles.
          </div>
        ) : (
          <ProviderProfilesManager
            profiles={visibleProviderProfiles}
            secretSlugs={(secretsQuery.data?.items ?? []).map((secret) => secret.slug)}
            onNotice={setNotice}
            queryClient={queryClient}
            defaultTaskModelByRuntime={defaultTaskModelByRuntime}
            canWriteProviderProfiles={canWriteProviderProfiles}
            selectedRuntimeId={providerProfileRuntimeFilter === ALL_RUNTIMES_FILTER_VALUE ? undefined : providerProfileRuntimeFilter}
            runtimeFilterOptions={providerProfileRuntimeOptions}
            onSelectRuntimeId={selectRuntime}
          />
        )
      ) : (
        <RegionUnavailable>
          Provider Profiles are unavailable because <code>provider_profiles.read</code> is not granted.
        </RegionUnavailable>
      )}

      {canReadSecretMetadata ? (
        secretsQuery.isLoading ? (
          <LoadingPlaceholder surface="settings" region="managed secrets" variant="table" density="compact" preserveContext />
        ) : secretsQuery.isError ? (
          <div className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700 shadow-sm dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400">
            Failed to load managed secrets.
          </div>
        ) : (
          <SecretManager
            secrets={secretsQuery.data?.items ?? []}
            onNotice={setNotice}
            queryClient={queryClient}
            permissions={permissions}
          />
        )
      ) : (
        <RegionUnavailable>
          Managed Secret metadata is unavailable because <code>secrets.metadata.read</code> is not granted.
        </RegionUnavailable>
      )}

      <GithubTokenProbePanel canRunProbe={canRunGithubTokenProbe} onNotice={setNotice} />
    </SettingsPageFrame>
  );
}

export function ProvidersSecretsSettingsPage({ payload }: { payload: BootPayload }) {
  const permissions = settingsPermissions(payload);
  const canInspect =
    permissions.has('provider_profiles.read') ||
    permissions.has('secrets.metadata.read') ||
    permissions.has('settings.effective.read');

  return (
    <SettingsDraftGuardProvider>
      {canInspect ? (
        <ProvidersSecretsSettingsContent payload={payload} />
      ) : (
        <SettingsPageFrame
          title="Providers & Secrets"
          description="Manage launch-ready Provider Profiles, credential references, OAuth lifecycle, and managed secret metadata."
        >
          <SettingsUnavailableState title="Providers & Secrets" permissions={['provider_profiles.read', 'secrets.metadata.read']} />
        </SettingsPageFrame>
      )}
    </SettingsDraftGuardProvider>
  );
}

function UserWorkspaceSettingsContent({ payload }: { payload: BootPayload }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [notice, setNotice] = useState<Notice | null>(null);
  const permissions = settingsPermissions(payload);
  const scope: SettingScope = searchParams.get('scope') === 'user' ? 'user' : 'workspace';
  const canWriteScope = permissions.has(`settings.${scope}.write`);

  const profileQuery = useQuery<ProfileData>({
    queryKey: ['profile'],
    queryFn: async () => {
      const response = await fetch('/me', {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`Failed to fetch profile: ${response.statusText}`);
      return response.json();
    },
  });

  const changeScope = (nextScope: SettingScope) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set('scope', nextScope);
      return next;
    });
  };

  return (
    <SettingsPageFrame
      title="User / Workspace"
      description="Review descriptor-driven preferences and defaults at user or workspace scope, including validation and application diagnostics."
    >
      <section aria-label="User and workspace settings summary" className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
              {scope === 'user' ? 'User scope' : 'Workspace scope'}
            </h3>
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
              {canWriteScope
                ? 'Overrides can be reviewed and saved at this scope.'
                : 'Safe inspection is available; changes at this scope are read-only.'}
            </p>
          </div>
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700 dark:bg-slate-800 dark:text-slate-300">
            {canWriteScope ? 'Writable' : 'Read-only'}
          </span>
        </div>
      </section>

      <NoticeBanner notice={notice} />
      <GeneratedSettingsSection scope={scope} onScopeChange={changeScope} />

      <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        <h3 className="text-base font-semibold text-slate-900 dark:text-white">
          Dashboard preferences
        </h3>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          Clear browser-local list layouts, selected workflows and recurring schedules, and other dashboard choices.
        </p>
        <button
          type="button"
          className="mt-4 rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
          onClick={() => {
            resetDashboardPreferences();
            setNotice({ level: 'ok', text: 'Dashboard preferences reset.' });
          }}
        >
          Reset dashboard preferences
        </button>
      </section>

      <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        {profileQuery.isLoading ? (
          <LoadingPlaceholder surface="settings" region="current user" variant="settings" density="normal" preserveContext />
        ) : profileQuery.isError ? (
          <p className="text-sm text-rose-700 dark:text-rose-400">Failed to load profile data.</p>
        ) : (
          <div>
            <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Signed-in user</div>
            <div className="mt-2 text-base font-semibold text-slate-900 dark:text-white">
              {profileQuery.data?.email || 'Unknown user'}
            </div>
            {profileQuery.data?.id ? (
              <div className="mt-1 font-mono text-xs text-slate-500 dark:text-slate-400">
                {profileQuery.data.id}
              </div>
            ) : null}
          </div>
        )}
      </section>
    </SettingsPageFrame>
  );
}

export function UserWorkspaceSettingsPage({ payload }: { payload: BootPayload }) {
  const canInspect = settingsPermissions(payload).has('settings.catalog.read');
  return (
    <SettingsDraftGuardProvider>
      {canInspect ? (
        <UserWorkspaceSettingsContent payload={payload} />
      ) : (
        <SettingsPageFrame
          title="User / Workspace"
          description="Review descriptor-driven preferences and defaults at user or workspace scope, including validation and application diagnostics."
        >
          <SettingsUnavailableState title="User / Workspace" permissions={['settings.catalog.read']} />
        </SettingsPageFrame>
      )}
    </SettingsDraftGuardProvider>
  );
}

export function OperationsSettingsPage({ payload }: { payload: BootPayload }) {
  const permissions = settingsPermissions(payload);
  const canInspect = permissions.has('operations.read');
  const workerPauseConfig = settingsInitialData(payload).workerPause ?? null;

  return (
    <SettingsDraftGuardProvider>
      <SettingsPageFrame
        title="Operations"
        description="Inspect worker, queue, runtime, deployment, and maintenance state and run authorized operational commands."
      >
        {canInspect ? (
          <OperationsSettingsSection
            workerPauseConfig={workerPauseConfig}
            canInvokeOperations={permissions.has('operations.invoke')}
          />
        ) : (
          <SettingsUnavailableState title="Operations" permissions={['operations.read']} />
        )}
      </SettingsPageFrame>
    </SettingsDraftGuardProvider>
  );
}

export function SettingsEntryPage({ payload }: { payload: BootPayload }) {
  const permissions = settingsPermissions(payload);
  if (
    permissions.has('provider_profiles.read') ||
    permissions.has('secrets.metadata.read') ||
    permissions.has('settings.effective.read')
  ) {
    return <Navigate to="/settings/providers-secrets" replace />;
  }
  if (permissions.has('settings.catalog.read')) {
    return <Navigate to="/settings/user-workspace" replace />;
  }
  if (permissions.has('operations.read')) {
    return <Navigate to="/settings/operations" replace />;
  }
  return (
    <SettingsPageFrame
      title="Configuration"
      description="No configuration destination is available for this account."
    >
      <SettingsUnavailableState
        title="Configuration"
        permissions={['provider_profiles.read', 'settings.catalog.read', 'operations.read']}
      />
    </SettingsPageFrame>
  );
}
