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
  });

  const providerProfilesQuery = useQuery<ProviderProfile[]>({
    queryKey: PROVIDER_PROFILE_QUERY_KEY,
    queryFn: fetchProviderProfiles,
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
      <ConfigurationHealthSummary
        providerProfiles={allProviderProfiles}
        secrets={secretsQuery.data?.items ?? []}
        isLoading={providerProfilesQuery.isLoading || secretsQuery.isLoading}
        isError={providerProfilesQuery.isError || secretsQuery.isError}
        canWriteProviderProfiles
        canRunGithubTokenProbe
      />

      <NoticeBanner notice={notice} />

      {providerProfilesQuery.isLoading ? (
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
          selectedRuntimeId={providerProfileRuntimeFilter === ALL_RUNTIMES_FILTER_VALUE ? undefined : providerProfileRuntimeFilter}
          runtimeFilterOptions={providerProfileRuntimeOptions}
          onSelectRuntimeId={selectRuntime}
        />
      )}

      {secretsQuery.isLoading ? (
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
        />
      )}

      <SourceControlConnections canRunProbe onNotice={setNotice} />
    </SettingsPageFrame>
  );
}

export function ProvidersSecretsSettingsPage({ payload }: { payload: BootPayload }) {
  return (
    <SettingsDraftGuardProvider>
      <ProvidersSecretsSettingsContent payload={payload} />
    </SettingsDraftGuardProvider>
  );
}

function InstanceSettingsContent() {
  const [notice, setNotice] = useState<Notice | null>(null);

  return (
    <SettingsPageFrame
      title="Instance"
      description="Review instance configuration and defaults, including validation and application diagnostics."
    >
      <NoticeBanner notice={notice} />
      <GeneratedSettingsSection />
      <p className="text-xs text-slate-500 dark:text-slate-400">
        This page edits instance-wide settings. The guarded upgrade reconciles
        per-user overrides retained from an account installation.
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

export function InstanceSettingsPage(_props: { payload: BootPayload }) {
  return (
    <SettingsDraftGuardProvider>
      <InstanceSettingsContent />
    </SettingsDraftGuardProvider>
  );
}

export function OperationsSettingsPage({ payload }: { payload: BootPayload }) {
  const workerPauseConfig = settingsInitialData(payload).workerPause ?? null;

  return (
    <SettingsDraftGuardProvider>
      <SettingsPageFrame
        title="Operations"
        description="Inspect worker, queue, runtime, deployment, and maintenance state and run operational commands."
      >
        <OperationsSettingsSection workerPauseConfig={workerPauseConfig} />
      </SettingsPageFrame>
    </SettingsDraftGuardProvider>
  );
}

export function SettingsEntryPage(_props: { payload: BootPayload }) {
  const { search } = useLocation();
  return <Navigate to={filterSettingsQueryForTarget(search, '/settings/providers-secrets')} replace />;
}
