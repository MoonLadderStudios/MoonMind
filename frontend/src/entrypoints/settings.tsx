import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BootPayload } from '../boot/parseBootPayload';
import { SecretManager } from '../components/secrets/SecretManager';
import { GeneratedSettingsSection } from '../components/settings/GeneratedSettingsSection';
import {
  OperationsSettingsSection,
  type WorkerPauseConfig,
} from '../components/settings/OperationsSettingsSection';
import {
  PROVIDER_PROFILE_QUERY_KEY,
  ProviderProfilesManager,
  type ProviderProfile,
} from '../components/settings/ProviderProfilesManager';

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

export function SettingsPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<Notice | null>(null);
  const [section, setSection] = useState<SettingsSectionId>(() => readSectionFromLocation());
  const workerPauseConfig =
    (payload.initialData as { workerPause?: WorkerPauseConfig } | undefined)?.workerPause ??
    null;
  const defaultTaskModelByRuntime: Record<string, string> =
    (payload.initialData as { runtimeConfig?: { system?: { defaultTaskModelByRuntime?: Record<string, string> } } } | undefined)
      ?.runtimeConfig?.system?.defaultTaskModelByRuntime ?? {};

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
      <header className="rounded-[2rem] border border-mm-border/80 bg-transparent px-6 py-6 shadow-sm">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
            Mission Control Settings
          </p>
          <h2 className="text-3xl font-semibold tracking-tight text-slate-950 dark:text-white">Settings</h2>
          <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">{currentSection.description}</p>
        </div>
      </header>

      <section className="rounded-[2rem] border border-mm-border/80 bg-transparent p-3 shadow-sm">
        <fieldset className="queue-step-type-field">
          <legend className="sr-only">Settings section</legend>
          <div className="queue-step-type-options">
            {SETTINGS_SECTIONS.map((candidate) => (
              <label
                key={candidate.id}
                className="queue-step-type-option"
                title={candidate.description}
              >
                <input
                  type="radio"
                  name="settings-section"
                  value={candidate.id}
                  checked={candidate.id === section}
                  onChange={() => handleSelectSection(candidate.id)}
                />
                <span className="queue-step-type-option-label">
                  {candidate.label}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      </section>

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
                <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Providers & Secrets</h3>
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
            <div className="rounded-3xl border border-mm-border/80 bg-transparent p-6 text-sm text-slate-500 dark:text-slate-400 shadow-sm">
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
              defaultTaskModelByRuntime={defaultTaskModelByRuntime}
            />
          )}

          {areSecretsLoading ? (
            <div className="rounded-3xl border border-mm-border/80 bg-transparent p-6 text-sm text-slate-500 dark:text-slate-400 shadow-sm">
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
          <GeneratedSettingsSection />

          <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
            {isLoading ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">Loading current user...</p>
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
