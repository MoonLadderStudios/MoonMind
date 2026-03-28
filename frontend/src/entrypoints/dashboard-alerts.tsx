import { useQuery } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';

interface SecretMetadata {
  slug: string;
  status: string;
}
interface SecretsListResponse {
  items: SecretMetadata[];
}

function DashboardAlerts() {
  const { data: secretsData, isLoading } = useQuery<SecretsListResponse>({
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

  if (isLoading || !secretsData) {
    return null;
  }

  // Check if we have essential keys
  const hasAnthropic = secretsData.items.some(s => s.slug === 'ANTHROPIC_API_KEY' && s.status === 'active');
  const hasOpenAI = secretsData.items.some(s => s.slug === 'OPENAI_API_KEY' && s.status === 'active');
  const hasGithub = secretsData.items.some(s => s.slug === 'GITHUB_PAT' && s.status === 'active');

  const needsAiKey = !hasAnthropic && !hasOpenAI;
  
  if (!needsAiKey && hasGithub) {
    return null;
  }

  return (
    <div className="notice notice-warning" style={{ marginBottom: '20px' }}>
      <strong>First-Run Setup:</strong> You are missing crucial API keys to run agent tasks.
      <ul style={{ marginTop: '8px', marginLeft: '20px', listStyleType: 'disc' }}>
         {needsAiKey && <li>A Provider API Key (e.g. <code>ANTHROPIC_API_KEY</code> or <code>OPENAI_API_KEY</code>) is missing.</li>}
         {!hasGithub && <li>A GitHub Personal Access Token (<code>GITHUB_PAT</code>) is missing.</li>}
      </ul>
      <div style={{ marginTop: '12px' }}>
         <a href="/tasks/secrets" className="btn btn-sm btn-outline">Go to Secrets Manager</a>
      </div>
    </div>
  );
}

// Mount to a specific alert root
mountPage(DashboardAlerts, 'dashboard-alerts-root');
