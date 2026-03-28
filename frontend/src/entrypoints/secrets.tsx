import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';
import { SecretManager } from '../components/secrets/SecretManager';

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

interface Notice {
  level: 'ok' | 'error';
  text: string;
}

function SecretsDashboardPage() {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<Notice | null>(null);

  const { data: secretsData, isLoading, isError } = useQuery<SecretsListResponse>({
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
  });

  return (
    <div className="view-container">
      <header className="view-header">
        <h2 className="view-title">Secrets Manager</h2>
        <p className="view-description">Manage API keys and other credentials securely.</p>
      </header>

      {isLoading ? (
        <p className="loading">Loading secrets...</p>
      ) : isError ? (
        <div className="notice notice-error">Failed to load secrets.</div>
      ) : (
        <div className="view-content">
          {notice && (
            <div className={`notice notice-${notice.level}`}>
              {notice.text}
            </div>
          )}
          
          <SecretManager 
            secrets={secretsData?.items || []} 
            onNotice={setNotice} 
            queryClient={queryClient} 
          />
        </div>
      )}
    </div>
  );
}

mountPage(SecretsDashboardPage);
