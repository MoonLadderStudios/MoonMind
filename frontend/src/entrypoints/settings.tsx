import { Button } from '../components/Button';
import { runtimeUnavailableReason } from "../runtime/runtimeTargets";
import { useMemo, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate, useLocation, useSearchParams } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { LoadingPlaceholder } from '../components/dashboard/LoadingPlaceholder';
import { PageHeader } from '../components/dashboard/PageHeader';
import { SecretManager } from '../components/secrets/SecretManager';
import { ConfigurationHealthSummary } from '../components/settings/ConfigurationHealthSummary';
import {
  GeneratedSettingsSection,
} from '../components/settings/GeneratedSettingsSection';
import {
  OperationsSettingsSection,
  type WorkerPauseConfig,
} from '../components/settings/OperationsSettingsSection';
import {
  ALL_RUNTIMES_FILTER_VALUE,
  fetchProviderProfiles,
  PROVIDER_PROFILE_DRAFT_ID,
  PROVIDER_PROFILE_QUERY_KEY,
  ProviderProfilesManager,
  type ProviderProfile,
} from '../components/settings/ProviderProfilesManager';
import { SourceControlConnections } from '../components/settings/SourceControlConnections';
import {
  SettingsDraftGuardProvider,
  useSettingsDraftGuard,
} from '../components/settings/SettingsDraftGuard';
import { responseError } from '../lib/api/client';
import { filterSettingsQueryForTarget } from '../lib/dashboardRoutes';
import { resetDashboardPreferences } from '../utils/dashboardPreferences';

const NON_PROFILE_OWNING_RUNTIMES = new Set(['omnigent']);

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
  return (
    <div className="settings-page mx-auto w-full space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader eyebrow="Configuration" title={title} description={description} />
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
        This instance destination cannot be inspected with the current access.
        Direct navigation remains on this route so the authorization boundary
        is explicit.
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
      if (!response.ok) throw await responseError(response, 'Failed to fetch secrets');
      return response.json();
    },
    enabled: canReadSecretMetadata,
  });

  const providerProfilesQuery = useQuery<ProviderProfile[]>({
    queryKey: PROVIDER_PROFILE_QUERY_KEY,
    queryFn: fetchProviderProfiles,
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
        !runtimeUnavailableReason(canonical) &&
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
    }, 'Change the Harness filter? Your unsaved Provider Profile draft will be discarded.', [
      PROVIDER_PROFILE_DRAFT_ID,
    ]);
  };

  return (
    <SettingsPageFrame
      title="Providers & Secrets"
      description="Manage Provider Profiles and the secrets they use. Secret values are never shown after they are saved."
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

      <SourceControlConnections canRunProbe={canRunGithubTokenProbe} onNotice={setNotice} />
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
          description="Manage Provider Profiles and the secrets they use. Secret values are never shown after they are saved."
        >
          <SettingsUnavailableState title="Providers & Secrets" permissions={['provider_profiles.read', 'secrets.metadata.read']} />
        </SettingsPageFrame>
      )}
    </SettingsDraftGuardProvider>
  );
}

function InstanceSettingsContent({ payload }: { payload: BootPayload }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const permissions = settingsPermissions(payload);

  return (
    <SettingsPageFrame
      title="Instance"
      description="Review instance configuration and defaults, including validation and application diagnostics."
    >
      <NoticeBanner notice={notice} />
      <GeneratedSettingsSection
        scope="workspace"
        canReadAudit={permissions.has('settings.audit.read')}
      />
      <p className="text-xs text-slate-500 dark:text-slate-400">
        This page edits instance-wide settings. Older per-user overrides are
        kept but not edited here.
      </p>

      <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        <h3 className="text-base font-semibold text-slate-900 dark:text-white">
          Dashboard preferences
        </h3>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          Clear browser-local list layouts, selected workflows and recurring schedules, and other dashboard choices.
        </p>
        <Button variant="ghost"
          type="button"
          className="mt-4 text-sm"
          onClick={() => {
            resetDashboardPreferences();
            setNotice({ level: 'ok', text: 'Dashboard preferences reset.' });
          }}
        >
          Reset dashboard preferences
        </Button>
      </section>
    </SettingsPageFrame>
  );
}

export function InstanceSettingsPage({ payload }: { payload: BootPayload }) {
  const canInspect = settingsPermissions(payload).has('settings.catalog.read');
  return (
    <SettingsDraftGuardProvider>
      {canInspect ? (
        <InstanceSettingsContent payload={payload} />
      ) : (
        <SettingsPageFrame
          title="Instance"
          description="Review instance configuration and defaults, including validation and application diagnostics."
        >
          <SettingsUnavailableState title="Instance" permissions={['settings.catalog.read']} />
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
  const { search } = useLocation();
  // SettingsPage.md 5.2: /settings is an entry point, not a destination. It
  // resolves with replacement history to the first authorized Configuration
  // page, keeping only that page's approved filters. `section` is retired page
  // identity (5.3) and is dropped rather than mapped back to a destination.
  const entryTarget = (target: string) => filterSettingsQueryForTarget(search, target);
  if (
    permissions.has('provider_profiles.read') ||
    permissions.has('secrets.metadata.read') ||
    permissions.has('settings.effective.read')
  ) {
    return <Navigate to={entryTarget('/settings/providers-secrets')} replace />;
  }
  if (permissions.has('settings.catalog.read')) {
    return <Navigate to={entryTarget('/settings/instance')} replace />;
  }
  if (permissions.has('operations.read')) {
    return <Navigate to={entryTarget('/settings/operations')} replace />;
  }
  return (
    <SettingsPageFrame
      title="Configuration"
      description="No configuration destination is available for this instance."
    >
      <SettingsUnavailableState
        title="Configuration"
        permissions={['provider_profiles.read', 'settings.catalog.read', 'operations.read']}
      />
    </SettingsPageFrame>
  );
}
