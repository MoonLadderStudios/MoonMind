import { useQuery } from '@tanstack/react-query';

import { isBrokenReferenceStatus } from '../secrets/SecretManager';
import type { WorkerPauseConfig } from './OperationsSettingsSection';
import type { ProviderProfile } from './ProviderProfilesManager';

/**
 * MM-965 — Settings configuration health summary.
 *
 * Aggregates boot/query data already loaded by the Settings page into a single
 * launch-readiness answer: "Is MoonMind configured well enough to run workflows
 * safely?" It surfaces provider/secret counts, missing or invalid defaults, and
 * permission/read-only states with visible reasons. It deliberately reuses the
 * data the page already fetches and the shared worker snapshot query rather than
 * introducing a dedicated backend health endpoint (out of scope).
 */

export interface HealthSecret {
  slug: string;
  status: string;
}

export type HealthLevel = 'ready' | 'warning' | 'blocked';

export interface HealthWarning {
  id: string;
  level: 'warning' | 'blocked';
  message: string;
}

export interface ConfigurationHealthInput {
  providerProfiles: ProviderProfile[];
  secrets: HealthSecret[];
  workerPauseConfigured: boolean;
  workersPaused?: boolean | null;
  workerMode?: string | null;
}

export interface ConfigurationHealthSummaryData {
  level: HealthLevel;
  headline: string;
  providerProfileCount: number;
  enabledProviderProfileCount: number;
  hasDefaultProfile: boolean;
  defaultProfileLabel: string | null;
  managedSecretCount: number;
  brokenSecretCount: number;
  warnings: HealthWarning[];
}

function profileLabel(profile: ProviderProfile): string {
  return profile.provider_label?.trim() || profile.profile_id;
}

/**
 * Extract the secret slug from a `<backend>://<locator>` secret reference.
 * For managed (`db://`) references the locator is the secret slug; values
 * without a scheme are treated as bare slugs.
 */
function secretSlugFromRef(ref: string): string | null {
  const value = ref?.trim();
  if (!value) {
    return null;
  }
  const separator = value.indexOf('://');
  const locator = separator >= 0 ? value.slice(separator + 3) : value;
  return locator.trim() || null;
}

/**
 * Pure derivation of configuration health from already-loaded settings data.
 * Kept separate from the component so it can be unit-tested without React.
 */
export function summarizeConfigurationHealth(
  input: ConfigurationHealthInput,
): ConfigurationHealthSummaryData {
  const {
    providerProfiles,
    secrets,
    workerPauseConfigured,
    workersPaused,
    workerMode,
  } = input;

  const enabledProfiles = providerProfiles.filter((profile) => profile.enabled);
  const defaultProfile =
    providerProfiles.find((profile) => profile.is_default && profile.enabled) ??
    providerProfiles.find((profile) => profile.is_default) ??
    null;

  // Only secrets referenced by an enabled profile can block a launch; an
  // unused disabled/rotated/deleted secret in the store is not a blocker.
  const referencedSecretSlugs = new Set<string>();
  for (const profile of enabledProfiles) {
    for (const ref of Object.values(profile.secret_refs ?? {})) {
      const slug = secretSlugFromRef(ref);
      if (slug) {
        referencedSecretSlugs.add(slug);
      }
    }
  }

  const brokenSecrets = secrets.filter((secret) =>
    isBrokenReferenceStatus(secret.status),
  );
  const blockingBrokenSecrets = brokenSecrets.filter((secret) =>
    referencedSecretSlugs.has(secret.slug),
  );
  const blockedReadinessProfiles = enabledProfiles.filter(
    (profile) => profile.readiness?.status === 'blocked',
  );

  const warnings: HealthWarning[] = [];

  if (providerProfiles.length === 0) {
    warnings.push({
      id: 'no-provider-profiles',
      level: 'blocked',
      message:
        'No provider profiles are configured. Add a provider profile before launching workflows.',
    });
  } else if (enabledProfiles.length === 0) {
    warnings.push({
      id: 'no-enabled-provider-profiles',
      level: 'blocked',
      message:
        'All provider profiles are disabled. Enable at least one profile to launch workflows.',
    });
  }

  if (providerProfiles.length > 0 && !defaultProfile) {
    warnings.push({
      id: 'no-default-profile',
      level: 'warning',
      message:
        'No default provider profile is set. Workflows without an explicit profile have no runtime to fall back to.',
    });
  }

  if (blockingBrokenSecrets.length > 0) {
    warnings.push({
      id: 'broken-secret-refs',
      level: 'blocked',
      message: `${blockingBrokenSecrets.length} managed secret${
        blockingBrokenSecrets.length === 1 ? '' : 's'
      } bound by enabled provider profiles ${
        blockingBrokenSecrets.length === 1 ? 'is' : 'are'
      } in a broken state (disabled, rotated, deleted, invalid, or missing). Profiles that bind them will fail launches.`,
    });
  }

  for (const profile of blockedReadinessProfiles) {
    warnings.push({
      id: `blocked-readiness-${profile.profile_id}`,
      level: 'blocked',
      message: `Provider profile "${profileLabel(profile)}" is not launch-ready: ${
        profile.readiness?.summary ?? 'readiness checks failed.'
      }`,
    });
  }

  if (!workerPauseConfigured) {
    warnings.push({
      id: 'worker-controls-unavailable',
      level: 'warning',
      message:
        'Worker operations controls are not configured for this deployment, so pause/resume state cannot be confirmed here.',
    });
  } else if (workersPaused) {
    const pausedDescriptor = workerMode === 'quiesce' ? 'quiesced' : 'paused';
    warnings.push({
      id: 'workers-paused',
      level: 'warning',
      message: `Workers are currently ${pausedDescriptor}, so newly launched workflows will not start until workers resume.`,
    });
  }

  const hasBlocking = warnings.some((warning) => warning.level === 'blocked');
  const level: HealthLevel = hasBlocking
    ? 'blocked'
    : warnings.length > 0
      ? 'warning'
      : 'ready';

  const headline =
    level === 'ready'
      ? 'MoonMind looks configured to run workflows.'
      : level === 'warning'
        ? 'MoonMind can run workflows, but some configuration needs attention.'
        : 'MoonMind is not ready to run workflows safely.';

  return {
    level,
    headline,
    providerProfileCount: providerProfiles.length,
    enabledProviderProfileCount: enabledProfiles.length,
    hasDefaultProfile: Boolean(defaultProfile),
    defaultProfileLabel: defaultProfile ? profileLabel(defaultProfile) : null,
    managedSecretCount: secrets.length,
    brokenSecretCount: brokenSecrets.length,
    warnings,
  };
}

interface WorkerStateSnapshot {
  system?: {
    workersPaused?: boolean;
    mode?: string | null;
  };
}

const LEVEL_BADGE: Record<HealthLevel, { label: string; className: string }> = {
  ready: {
    label: 'Launch ready',
    className:
      'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  },
  warning: {
    label: 'Needs attention',
    className: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  },
  blocked: {
    label: 'Not launch ready',
    className: 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300',
  },
};

function MetricTile({
  label,
  value,
  tone = 'default',
  hint,
}: {
  label: string;
  value: string;
  tone?: 'default' | 'warning';
  hint?: string;
}) {
  return (
    <div
      className={`rounded-2xl border p-4 ${
        tone === 'warning'
          ? 'border-amber-200 bg-amber-50 dark:border-amber-900/50 dark:bg-amber-900/20'
          : 'border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-800/50'
      }`}
    >
      <div className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-900 dark:text-white">{value}</div>
      {hint ? (
        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</div>
      ) : null}
    </div>
  );
}

function ReadOnlyBadge({ reason }: { reason: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-slate-300 bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
      title={reason}
    >
      Read-only
    </span>
  );
}

export interface ConfigurationHealthSummaryProps {
  providerProfiles: ProviderProfile[];
  secrets: HealthSecret[];
  isLoading?: boolean;
  isError?: boolean;
  workerPauseConfig: WorkerPauseConfig | null;
  canWriteProviderProfiles: boolean;
  canRunGithubTokenProbe: boolean;
}

export function ConfigurationHealthSummary({
  providerProfiles,
  secrets,
  isLoading = false,
  isError = false,
  workerPauseConfig,
  canWriteProviderProfiles,
  canRunGithubTokenProbe,
}: ConfigurationHealthSummaryProps) {
  const workerPauseConfigured = workerPauseConfig !== null;

  const { data: workerState } = useQuery<WorkerStateSnapshot>({
    queryKey: ['workers-snapshot'],
    queryFn: async () => {
      if (!workerPauseConfig?.get) {
        throw new Error('Worker pause GET endpoint is not configured.');
      }
      const response = await fetch(workerPauseConfig.get, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch worker status: ${response.statusText}`);
      }
      return (await response.json()) as WorkerStateSnapshot;
    },
    enabled: workerPauseConfigured && Boolean(workerPauseConfig?.get),
  });

  if (isLoading) {
    return (
      <section
        aria-label="Configuration health summary"
        className="rounded-3xl border border-mm-border/80 bg-transparent p-6 text-sm text-slate-500 shadow-sm dark:text-slate-400"
      >
        Loading configuration health...
      </section>
    );
  }

  if (isError) {
    return (
      <section
        aria-label="Configuration health summary"
        className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700 shadow-sm dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400"
      >
        Failed to load configuration health data.
      </section>
    );
  }

  const workersPaused = workerState?.system?.workersPaused ?? null;
  const workerMode = workerState?.system?.mode ?? null;

  const summary = summarizeConfigurationHealth({
    providerProfiles,
    secrets,
    workerPauseConfigured,
    workersPaused,
    workerMode,
  });

  const badge = LEVEL_BADGE[summary.level];

  let workerStateLabel: string;
  if (!workerPauseConfigured) {
    workerStateLabel = 'Not configured';
  } else if (workersPaused === null) {
    workerStateLabel = 'Unknown';
  } else if (workersPaused) {
    workerStateLabel = workerMode === 'quiesce' ? 'Quiesced' : 'Paused';
  } else {
    workerStateLabel = 'Running';
  }

  return (
    <section
      aria-label="Configuration health summary"
      className="rounded-[2rem] border border-mm-border/80 bg-transparent p-6 shadow-sm"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
            Configuration health
          </h3>
          <p className="max-w-2xl text-sm text-slate-600 dark:text-slate-400">
            {summary.headline}
          </p>
        </div>
        <span
          role="status"
          className={`inline-flex shrink-0 items-center rounded-full px-3 py-1 text-xs font-semibold ${badge.className}`}
        >
          {badge.label}
        </span>
      </div>

      <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricTile
          label="Provider profiles"
          value={String(summary.providerProfileCount)}
          hint={`${summary.enabledProviderProfileCount} enabled`}
          tone={summary.enabledProviderProfileCount === 0 ? 'warning' : 'default'}
        />
        <MetricTile
          label="Default profile"
          value={summary.hasDefaultProfile ? 'Configured' : 'Missing'}
          hint={summary.defaultProfileLabel ?? 'No default set'}
          tone={summary.hasDefaultProfile ? 'default' : 'warning'}
        />
        <MetricTile
          label="Managed secrets"
          value={String(summary.managedSecretCount)}
          hint={
            summary.brokenSecretCount > 0
              ? `${summary.brokenSecretCount} broken`
              : 'All references healthy'
          }
          tone={summary.brokenSecretCount > 0 ? 'warning' : 'default'}
        />
        <MetricTile label="Worker state" value={workerStateLabel} />
      </div>

      {summary.warnings.length > 0 ? (
        <ul
          aria-label="Configuration warnings"
          className="mt-5 space-y-2"
        >
          {summary.warnings.map((warning) => (
            <li
              key={warning.id}
              className={`rounded-2xl border px-4 py-3 text-sm ${
                warning.level === 'blocked'
                  ? 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400'
                  : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-300'
              }`}
            >
              {warning.message}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-5 text-sm text-emerald-700 dark:text-emerald-400">
          No missing or invalid defaults detected.
        </p>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-slate-200 pt-4 dark:border-slate-800">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Permissions
        </span>
        {canWriteProviderProfiles ? (
          <span className="text-xs text-slate-600 dark:text-slate-400">
            Provider profile writes enabled
          </span>
        ) : (
          <span className="inline-flex items-center gap-2 text-xs text-slate-600 dark:text-slate-400">
            <ReadOnlyBadge reason="Provider profile writes require the provider_profiles.write permission." />
            Provider profile writes disabled — requires the{' '}
            <code>provider_profiles.write</code> permission.
          </span>
        )}
        {canRunGithubTokenProbe ? (
          <span className="text-xs text-slate-600 dark:text-slate-400">
            GitHub token probe available
          </span>
        ) : (
          <span className="inline-flex items-center gap-2 text-xs text-slate-600 dark:text-slate-400">
            <ReadOnlyBadge reason="The GitHub token probe requires the settings.effective.read permission." />
            GitHub token probe unavailable — requires the{' '}
            <code>settings.effective.read</code> permission.
          </span>
        )}
      </div>
    </section>
  );
}

export default ConfigurationHealthSummary;
