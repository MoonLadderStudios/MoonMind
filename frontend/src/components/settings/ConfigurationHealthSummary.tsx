import { isBrokenReferenceStatus } from '../secrets/SecretManager';
import type { ProviderProfile } from './ProviderProfilesManager';
import { newWorkDefaultProblem, type NewWorkDefaults } from '../../lib/newWorkDefault';

export interface HealthSecret { slug: string; status: string }
export type HealthLevel = 'ready' | 'warning' | 'blocked';
export interface HealthTarget { kind: 'profile' | 'secret' | 'defaults'; id: string }
export interface HealthWarning {
  id: string;
  level: 'warning' | 'blocked';
  message: string;
  target: HealthTarget;
  action: string;
}
export interface ConfigurationHealthInput {
  providerProfiles: ProviderProfile[];
  secrets: HealthSecret[];
  newWorkDefaults: NewWorkDefaults;
}
export interface ConfigurationHealthSummaryData { level: HealthLevel; warnings: HealthWarning[] }

/** Only managed references (or bare managed slugs) have rows in Managed secrets. */
export function secretSlugFromRef(ref: string): string | null {
  const value = ref.trim();
  if (value.includes('://') && !value.startsWith('db://')) return null;
  return (value.startsWith('db://') ? value.slice(5) : value).trim() || null;
}

export function summarizeConfigurationHealth({
  providerProfiles, secrets, newWorkDefaults,
}: ConfigurationHealthInput): ConfigurationHealthSummaryData {
  const warnings: HealthWarning[] = [];
  const defaultProblem = newWorkDefaultProblem(newWorkDefaults, providerProfiles);
  if (defaultProblem) warnings.push({
    id: 'new-work-default', level: 'blocked', ...defaultProblem,
    target: { kind: 'defaults', id: 'new-work-defaults' },
  });
  const statusBySlug = new Map(secrets.map((secret) => [secret.slug, secret.status]));
  for (const profile of providerProfiles.filter((profile) => profile.enabled)) {
    for (const slug of new Set(Object.values(profile.secret_refs ?? {}).map(secretSlugFromRef))) {
      const status = slug ? statusBySlug.get(slug) : undefined;
      if (slug && isBrokenReferenceStatus(status)) warnings.push({
        id: `secret-${profile.profile_id}-${slug}`, level: 'blocked',
        message: `${profile.profile_id} can't launch because its secret ${slug} is ${status}.`,
        target: { kind: 'secret', id: slug }, action: 'Show secret',
      });
    }
    if (profile.readiness?.status === 'blocked') warnings.push({
      id: `readiness-${profile.profile_id}`, level: 'blocked',
      message: `${profile.profile_id} can't launch: ${profile.readiness.summary}`,
      target: { kind: 'profile', id: profile.profile_id }, action: 'Show profile',
    });
  }
  return { level: warnings.some((warning) => warning.level === 'blocked') ? 'blocked' : warnings.length ? 'warning' : 'ready', warnings };
}

export function healthTargetId(target: HealthTarget): string {
  return target.kind === 'profile' ? `provider-profile-${target.id}` :
    target.kind === 'secret' ? `managed-secret-${target.id}` : 'new-work-defaults';
}

export interface ConfigurationAlertProps extends ConfigurationHealthInput {
  isLoading?: boolean;
  isError?: boolean;
  canWriteProviderProfiles: boolean;
  canRunGithubTokenProbe: boolean;
  onNavigate?: (target: HealthTarget) => void;
}

export function ConfigurationAlert({
  providerProfiles, secrets, newWorkDefaults, isLoading, isError,
  canWriteProviderProfiles, canRunGithubTokenProbe, onNavigate,
}: ConfigurationAlertProps) {
  if (isLoading || isError) return null;
  const summary = summarizeConfigurationHealth({ providerProfiles, secrets, newWorkDefaults });
  if (!summary.warnings.length && canWriteProviderProfiles && canRunGithubTokenProbe) return null;
  return (
    <div role="alert" className={`rounded-2xl border p-4 text-sm ${summary.level === 'blocked'
      ? 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400'
      : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-300'}`}>
      <ul className="space-y-2" aria-label="Configuration problems">
        {summary.warnings.map((warning) => (
          <li key={warning.id}>
            {warning.message}{' '}
            <a className="underline" href={`#${healthTargetId(warning.target)}`}
              onClick={onNavigate ? (event) => { event.preventDefault(); onNavigate(warning.target); } : undefined}>
              {warning.action}
            </a>
          </li>
        ))}
        {!canWriteProviderProfiles ? <li>Provider profile writes disabled — requires the <code>provider_profiles.write</code> permission.</li> : null}
        {!canRunGithubTokenProbe ? <li>GitHub token probe unavailable — requires the <code>settings.effective.read</code> permission.</li> : null}
      </ul>
    </div>
  );
}
