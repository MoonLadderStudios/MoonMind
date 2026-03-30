import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';
import { BootPayload } from '../boot/parseBootPayload';
import { SecretManager } from '../components/secrets/SecretManager';
import {
  OperationsSettingsSection,
  type WorkerPauseConfig,
} from '../components/settings/OperationsSettingsSection';
import {
  ProviderProfilesManager,
  type ProviderProfile,
} from '../components/settings/ProviderProfilesManager';

interface ProfileData {
  id?: string;
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

const SETTINGS_SECTIONS = [
  {
    id: 'providers-secrets',
    label: 'Providers & Secrets',
    description:
      'Configure provider profiles, managed secrets, and the bindings that make runtimes launchable.',
  },
  {
    id: 'user-workspace',
    label: 'User / Workspace',
    description:
      'Hold user-scoped and workspace-scoped settings as Mission Control exposes more of the broader configuration model.',
  },
  {
    id: 'operations',
    label: 'Operations',
    description:
      'Keep worker pause, drain, quiesce, and related operational controls under Settings.',
  },
] as const;

type SettingsSectionId = (typeof SETTINGS_SECTIONS)[number]['id'];

function isSettingsSection(value: string | null): value is SettingsSectionId {
  return SETTINGS_SECTIONS.some((section) => section.id === value);
}

function readSectionFromLocation(): SettingsSectionId {
  const params = new URLSearchParams(window.location.search);
  const section = params.get('section');
  return isSettingsSection(section) ? section : 'providers-secrets';
}

function updateSectionInLocation(section: SettingsSectionId): void {
  const url = new URL(window.location.href);
  url.searchParams.set('section', section);
  window.history.pushState({}, '', url.toString());
}

function SettingsPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<Notice | null>(null);
  const [section, setSection] = useState<SettingsSectionId>(() => readSectionFromLocation());
  const workerPauseConfig =
    (payload.initialData as { workerPause?: WorkerPauseConfig } | undefined)?.workerPause ??
    null;

  useEffect(() => {
    const handlePopState = () => {
      setSection(readSectionFromLocation());
      setNotice(null);
    };

    window.addEventListener('popstate', handlePopState);
    return () => {
      window.removeEventListener('popstate', handlePopState);
    };
  }, []);

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
    enabled: section === 'providers-secrets',
  });

  const {
    data: providerProfiles,
    isLoading: areProfilesLoading,
    isError: areProfilesErrored,
  } = useQuery<ProviderProfile[]>({
    queryKey: ['provider-profiles'],
    queryFn: async () => {
      const response = await fetch('/api/v1/provider-profiles', {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch provider profiles: ${response.statusText}`);
      }
      return response.json();
    },
    enabled: section === 'providers-secrets',
  });

  const currentSection =
    SETTINGS_SECTIONS.find((candidate) => candidate.id === section) ?? SETTINGS_SECTIONS[0];

  const handleSelectSection = (nextSection: SettingsSectionId) => {
    if (nextSection === section) {
      return;
    }
    updateSectionInLocation(nextSection);
    setSection(nextSection);
    setNotice(null);
  };

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="rounded-[2rem] border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 dark:bg-slate-100 dark:border-slate-800 dark:bg-slate-900 dark:bg-slate-100 px-6 py-6 shadow-sm">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
            Mission Control Settings
          </p>
          <h2 className="text-3xl font-semibold tracking-tight text-slate-950 dark:text-white dark:text-slate-900">Settings</h2>
          <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">{currentSection.description}</p>
        </div>
      </header>

      <section className="rounded-[2rem] border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 dark:bg-slate-100 dark:border-slate-800 dark:bg-slate-900 dark:bg-slate-100 p-3 shadow-sm">
        <div className="flex flex-wrap gap-2">
          {SETTINGS_SECTIONS.map((candidate) => {
            const active = candidate.id === section;
            return (
              <button
                key={candidate.id}
                type="button"
                className={`rounded-full px-4 py-2 text-sm font-medium transition ${
                  active
                    ? 'bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 dark:bg-white dark:bg-slate-900 dark:bg-slate-100 dark:text-slate-900 dark:text-white dark:text-slate-900'
                    : 'border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 dark:bg-slate-100 text-slate-700 dark:text-slate-300 hover:border-slate-400 dark:hover:border-slate-500 hover:text-slate-900 dark:hover:text-white dark:text-slate-900 dark:text-white dark:text-slate-900'
                }`}
                onClick={() => handleSelectSection(candidate.id)}
              >
                {candidate.label}
              </button>
            );
          })}
        </div>
      </section>

      {notice ? (
        <div
          className={`rounded-3xl border px-5 py-4 text-sm shadow-sm ${
            notice.level === 'error'
              ? 'border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-400 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400'
              : 'border-emerald-200 dark:border-emerald-900/50 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-400'
          }`}
        >
          {notice.text}
        </div>
      ) : null}

      {section === 'providers-secrets' ? (
        <div className="space-y-6">
          <section className="rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 dark:bg-slate-100 dark:border-slate-800 dark:bg-slate-900 dark:bg-slate-100 p-6 shadow-sm">
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
              <div className="space-y-2">
                <h3 className="text-lg font-semibold text-slate-900 dark:text-white dark:text-slate-900">Providers & Secrets</h3>
                <p className="text-sm text-slate-600 dark:text-slate-400">
                  Provider profiles are the durable runtime and provider launch contract.
                  Managed secrets back those profiles without re-exposing raw credential
                  values after creation.
                </p>
              </div>
              <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800 dark:bg-slate-200/50 dark:border-slate-800 dark:bg-slate-800 dark:bg-slate-200/50 p-4 text-sm text-slate-600 dark:text-slate-400">
                Use secret refs such as <code>db://OPENAI_API_KEY</code> inside provider
                profiles. Secrets stay in the managed secret store; profiles only keep the
                refs and launch metadata.
              </div>
            </div>
          </section>

          {areProfilesLoading ? (
            <div className="rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 dark:bg-slate-100 dark:border-slate-800 dark:bg-slate-900 dark:bg-slate-100 p-6 text-sm text-slate-500 dark:text-slate-400 shadow-sm">
              Loading provider profiles...
            </div>
          ) : areProfilesErrored ? (
            <div className="rounded-3xl border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-900/20 p-6 text-sm text-rose-700 dark:text-rose-400 shadow-sm">
              Failed to load provider profiles.
            </div>
          ) : (
            <ProviderProfilesManager
              profiles={providerProfiles ?? []}
              secretSlugs={(secretsData?.items ?? []).map((secret) => secret.slug)}
              onNotice={setNotice}
              queryClient={queryClient}
            />
          )}

          {areSecretsLoading ? (
            <div className="rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 dark:bg-slate-100 dark:border-slate-800 dark:bg-slate-900 dark:bg-slate-100 p-6 text-sm text-slate-500 dark:text-slate-400 shadow-sm">
              Loading managed secrets...
            </div>
          ) : areSecretsErrored ? (
            <div className="rounded-3xl border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-900/20 p-6 text-sm text-rose-700 dark:text-rose-400 shadow-sm">
              Failed to load managed secrets.
            </div>
          ) : (
            <SecretManager
              secrets={secretsData?.items ?? []}
              onNotice={setNotice}
              queryClient={queryClient}
            />
          )}
        </div>
      ) : null}

      {section === 'user-workspace' ? (
        <div className="space-y-6">
          <section className="rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 dark:bg-slate-100 dark:border-slate-800 dark:bg-slate-900 dark:bg-slate-100 p-6 shadow-sm">
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white dark:text-slate-900">User / Workspace</h3>
            <p className="mt-2 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
              This section is reserved for the broader project settings model. The
              unified Settings tab now provides a stable home for those controls as
              Mission Control exposes more runtime, workspace, and operator preferences.
            </p>
          </section>

          <section className="rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 dark:bg-slate-100 dark:border-slate-800 dark:bg-slate-900 dark:bg-slate-100 p-6 shadow-sm">
            {isLoading ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">Loading current user...</p>
            ) : isError ? (
              <p className="text-sm text-rose-700 dark:text-rose-400">Failed to load profile data.</p>
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800 dark:bg-slate-200/50 dark:border-slate-800 dark:bg-slate-800 dark:bg-slate-200/50 p-5">
                  <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Signed-in user</div>
                  <div className="mt-2 text-base font-semibold text-slate-900 dark:text-white dark:text-slate-900">
                    {profile?.email || 'Unknown user'}
                  </div>
                  {profile?.id ? (
                    <div className="mt-1 font-mono text-xs text-slate-500 dark:text-slate-400">
                      {profile.id}
                    </div>
                  ) : null}
                </div>
                <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800 dark:bg-slate-200/50 dark:border-slate-800 dark:bg-slate-800 dark:bg-slate-200/50 p-5 text-sm text-slate-600 dark:text-slate-400">
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

mountPage(SettingsPage);
