import { useQuery } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';

interface SecretMetadata {
  slug: string;
  status: string;
}
interface SecretsListResponse {
  items: SecretMetadata[];
}

interface ProviderProfileResponse {
  profile_id: string;
  credential_source: string;
  enabled: boolean;
}

/** Slugs stored in Managed Secrets (often match env var names after import). */
const PROVIDER_KEY_SLUGS = [
  'ANTHROPIC_API_KEY',
  'OPENAI_API_KEY',
  'MINIMAX_API_KEY',
] as const;

const GITHUB_TOKEN_SLUGS = ['GITHUB_PAT', 'GITHUB_TOKEN', 'GH_TOKEN'] as const;

function hasActiveSlug(items: SecretMetadata[], slugs: readonly string[]): boolean {
  return items.some((s) => slugs.includes(s.slug) && s.status === 'active');
}

function DashboardAlerts() {
  const { data: secretsData, isLoading: secretsLoading } = useQuery<SecretsListResponse>({
    queryKey: ['secrets-alerts'],
    queryFn: async () => {
      const response = await fetch('/api/v1/secrets', {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error('Failed to fetch secrets');
      }
      return response.json();
    },
  });

  const { data: profilesData, isLoading: profilesLoading } = useQuery<ProviderProfileResponse[]>({
    queryKey: ['provider-profiles-alerts'],
    queryFn: async () => {
      const response = await fetch('/api/v1/provider-profiles', {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error('Failed to fetch provider profiles');
      }
      return response.json();
    },
  });

  if (secretsLoading || profilesLoading || !secretsData || !profilesData) {
    return null;
  }

  const hasProviderSecret = secretsData.items.some(
    (s) =>
      s.status === 'active' &&
      (PROVIDER_KEY_SLUGS.includes(s.slug as any) || s.slug.endsWith('_API_KEY'))
  );

  const hasOauthVolume = profilesData.some(
    (p) => p.enabled && p.credential_source === 'oauth_volume'
  );

  const hasProviderKey = hasProviderSecret || hasOauthVolume;
  const hasGithub = hasActiveSlug(secretsData.items, GITHUB_TOKEN_SLUGS);

  const needsAiKey = !hasProviderKey;
  
  if (!needsAiKey && hasGithub) {
    return null;
  }

  return (
    <div className="notice notice-warning" style={{ marginBottom: '20px' }}>
      <strong>First-Run Setup:</strong> You are missing crucial API keys to run agent tasks.
      <ul style={{ marginTop: '8px', marginLeft: '20px', listStyleType: 'disc' }}>
         {needsAiKey && (
           <li>
             A provider API key in Settings is missing (e.g.{' '}
             <code>ANTHROPIC_API_KEY</code>, <code>OPENAI_API_KEY</code>, or <code>MINIMAX_API_KEY</code>).
           </li>
         )}
         {!hasGithub && (
           <li>
             A GitHub token in Settings is missing (use slug <code>GITHUB_TOKEN</code>, <code>GH_TOKEN</code>, or{' '}
             <code>GITHUB_PAT</code> — runtime and docs use <code>GITHUB_TOKEN</code> most often).
           </li>
         )}
      </ul>
      <div style={{ marginTop: '12px' }}>
         <a href="/tasks/settings?section=providers-secrets" className="btn btn-sm btn-outline">Open Settings</a>
      </div>
    </div>
  );
}

// Mount to a specific alert root
mountPage(DashboardAlerts, 'dashboard-alerts-root');
