import { useQuery } from '@tanstack/react-query';

interface SecretMetadata {
  slug: string;
  status: string;
}
interface SecretsListResponse {
  items: SecretMetadata[];
}

interface ProviderProfileResponse {
  profile_id: string;
  enabled: boolean;
  launch_ready: boolean;
}

const GITHUB_TOKEN_SLUGS = ['GITHUB_PAT', 'GITHUB_TOKEN'] as const;

function hasActiveSlug(items: SecretMetadata[], slugs: readonly string[]): boolean {
  return items.some((s) => slugs.includes(s.slug) && s.status === 'active');
}

export function DashboardAlerts() {
  const { data: secretsData, isLoading: secretsLoading } = useQuery<SecretsListResponse>({
    queryKey: ['secrets'],
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

  const {
    data: profilesData,
    isLoading: profilesLoading,
    isError: profilesError,
  } = useQuery<ProviderProfileResponse[]>({
    queryKey: ['provider-profiles'],
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

  if (secretsLoading || profilesLoading || !secretsData || (!profilesData && !profilesError)) {
    return null;
  }

  const hasLaunchReadyProviderProfile = profilesData?.some((profile) => profile.launch_ready) ?? false;
  const hasGithub = hasActiveSlug(secretsData.items, GITHUB_TOKEN_SLUGS);
  const needsProviderProfileSetup = !hasLaunchReadyProviderProfile;

  if (!profilesError && !needsProviderProfileSetup && hasGithub) {
    return null;
  }

  return (
    <div className="notice notice-warning" style={{ marginBottom: '20px' }}>
      <strong>First-Run Setup:</strong> Complete setup before running agent tasks.
      <ul style={{ marginTop: '8px', marginLeft: '20px', listStyleType: 'disc' }}>
         {profilesError ? (
           <li>
             MoonMind could not verify provider profile readiness. Review Provider Profiles in Settings.
           </li>
         ) : needsProviderProfileSetup ? (
           <li>Set up and enable at least one provider profile in Settings.</li>
         ) : null}
         {!hasGithub && (
           <li>Set up GitHub access in Settings.</li>
         )}
      </ul>
      <div style={{ marginTop: '12px' }}>
         <a href="/settings?section=providers-secrets" className="btn btn-sm btn-outline">Open Settings</a>
      </div>
    </div>
  );
}
export default DashboardAlerts;
