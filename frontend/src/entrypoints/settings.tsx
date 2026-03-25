import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';
import { BootPayload } from '../boot/parseBootPayload';

interface ProfileData {
  id?: string;
  email?: string;
  anthropic_api_key_set?: boolean;
  openai_api_key_set?: boolean;
  google_api_key_set?: boolean;
}

interface Notice {
  level: 'ok' | 'error';
  text: string;
}

function UserSettingsPage(_props: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<Notice | null>(null);

  // Form states for API keys
  const [openaiKey, setOpenaiKey] = useState('');
  const [googleKey, setGoogleKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');

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
  });

  const updateProfileMutation = useMutation({
    mutationFn: async (updates: Record<string, string>) => {
      const response = await fetch('/me/profile', {
        method: 'PATCH',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(updates),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `Server error: ${response.status}`);
      }

      return response.json();
    },
    onSuccess: () => {
      setNotice({ level: 'ok', text: 'API keys updated successfully.' });
      setOpenaiKey('');
      setGoogleKey('');
      setAnthropicKey('');
      queryClient.invalidateQueries({ queryKey: ['profile'] });
    },
    onError: (error: Error) => {
      setNotice({ level: 'error', text: error.message || 'Failed to update API keys.' });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setNotice(null);

    const updates: Record<string, string> = {};
    if (openaiKey.trim()) updates.openai_api_key = openaiKey.trim();
    if (googleKey.trim()) updates.google_api_key = googleKey.trim();
    if (anthropicKey.trim()) updates.anthropic_api_key = anthropicKey.trim();

    if (Object.keys(updates).length === 0) {
      setNotice({ level: 'ok', text: 'No changes to save.' });
      return;
    }

    updateProfileMutation.mutate(updates);
  };

  return (
    <div className="view-container">
      <header className="view-header">
        <h2 className="view-title">Settings</h2>
        <p className="view-description">Manage your profile settings and API keys.</p>
      </header>

      {isLoading ? (
        <p className="loading">Loading profile settings...</p>
      ) : isError ? (
        <div className="notice notice-error">Failed to load profile data.</div>
      ) : (
        <div className="view-content">
          {notice && (
            <div data-user-settings-notice>
              <div className={`notice notice-${notice.level}`}>
                {notice.text}
              </div>
            </div>
          )}

          <div className="system-settings-grid">
            <section className="card system-settings-forms" style={{ gridColumn: '1 / -1' }}>
              <div className="card-header">
                <h3>User Settings</h3>
              </div>
              <div className="card-body">
                <form data-user-settings-form className="stack" onSubmit={handleSubmit}>
                  <div className="field">
                    <label htmlFor="openai_api_key">OpenAI API Key</label>
                    <input
                      type="password"
                      id="openai_api_key"
                      name="openai_api_key"
                      placeholder={profile?.openai_api_key_set ? '••••••••' : 'sk-...'}
                      value={openaiKey}
                      onChange={(e) => setOpenaiKey(e.target.value)}
                      disabled={updateProfileMutation.isPending}
                    />
                    <div className="field-hint">Leave blank to keep existing key.</div>
                  </div>

                  <div className="field">
                    <label htmlFor="google_api_key">Google (Gemini) API Key</label>
                    <input
                      type="password"
                      id="google_api_key"
                      name="google_api_key"
                      placeholder={profile?.google_api_key_set ? '••••••••' : 'AIza...'}
                      value={googleKey}
                      onChange={(e) => setGoogleKey(e.target.value)}
                      disabled={updateProfileMutation.isPending}
                    />
                    <div className="field-hint">Leave blank to keep existing key.</div>
                  </div>

                  <div className="field">
                    <label htmlFor="anthropic_api_key">Anthropic API Key</label>
                    <input
                      type="password"
                      id="anthropic_api_key"
                      name="anthropic_api_key"
                      placeholder={profile?.anthropic_api_key_set ? '••••••••' : 'sk-ant-...'}
                      value={anthropicKey}
                      onChange={(e) => setAnthropicKey(e.target.value)}
                      disabled={updateProfileMutation.isPending}
                    />
                    <div className="field-hint">Leave blank to keep existing key.</div>
                  </div>

                  <button
                    type="submit"
                    className="settings-submit-btn"
                    disabled={updateProfileMutation.isPending}
                  >
                    {updateProfileMutation.isPending ? 'Saving...' : 'Save API Keys'}
                  </button>
                </form>
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}

mountPage(UserSettingsPage);
