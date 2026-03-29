import { FormEvent, useState } from 'react';
import { QueryClient, useMutation } from '@tanstack/react-query';

interface SecretMetadata {
  slug: string;
  status: string;
  details: Record<string, unknown>;
  createdAt: string;
  updatedAt?: string;
}

interface SecretManagerProps {
  secrets: SecretMetadata[];
  onNotice: (notice: { level: 'ok' | 'error'; text: string } | null) => void;
  queryClient: QueryClient;
}

export function SecretManager({ secrets, onNotice, queryClient }: SecretManagerProps) {
  const [slug, setSlug] = useState('');
  const [plaintext, setPlaintext] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [rotatePromptOpen, setRotatePromptOpen] = useState(false);
  const [rotatePromptSlug, setRotatePromptSlug] = useState('');
  const [rotatePromptVal, setRotatePromptVal] = useState('');

  const createOp = useMutation({
    mutationFn: async ({
      slug: nextSlug,
      plaintext: nextPlaintext,
    }: {
      slug: string;
      plaintext: string;
    }) => {
      const response = await fetch('/api/v1/secrets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug: nextSlug, plaintext: nextPlaintext, details: {} }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to create secret');
      }
      return response.json();
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret saved successfully.' });
      setSlug('');
      setPlaintext('');
      setIsEditing(false);
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
    },
    onError: (error: Error) => onNotice({ level: 'error', text: error.message }),
  });

  const updateOp = useMutation({
    mutationFn: async ({
      slug: nextSlug,
      plaintext: nextPlaintext,
    }: {
      slug: string;
      plaintext: string;
    }) => {
      const response = await fetch(`/api/v1/secrets/${nextSlug}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plaintext: nextPlaintext }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to update secret');
      }
      return response.json();
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret updated successfully.' });
      setSlug('');
      setPlaintext('');
      setIsEditing(false);
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
    },
    onError: (error: Error) => onNotice({ level: 'error', text: error.message }),
  });

  const rotateOp = useMutation({
    mutationFn: async ({
      slug: nextSlug,
      plaintext: nextPlaintext,
    }: {
      slug: string;
      plaintext: string;
    }) => {
      const response = await fetch(`/api/v1/secrets/${nextSlug}/rotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plaintext: nextPlaintext }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to rotate secret');
      }
      return response.json();
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret rotated successfully.' });
      setSlug('');
      setPlaintext('');
      setIsEditing(false);
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
    },
    onError: (error: Error) => onNotice({ level: 'error', text: error.message }),
  });

  const deleteOp = useMutation({
    mutationFn: async (targetSlug: string) => {
      const response = await fetch(`/api/v1/secrets/${targetSlug}`, { method: 'DELETE' });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to delete secret');
      }
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret deleted successfully.' });
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
    },
    onError: (error: Error) => onNotice({ level: 'error', text: error.message }),
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!slug) {
      onNotice({ level: 'error', text: 'Secret slug is required.' });
      return;
    }
    if (!plaintext) {
      onNotice({
        level: 'error',
        text: isEditing
          ? 'Provide a new secure value to update the secret.'
          : 'Provide a secure value for the new secret.',
      });
      return;
    }
    if (isEditing) {
      updateOp.mutate({ slug, plaintext });
      return;
    }
    createOp.mutate({ slug, plaintext });
  };

  const renderStatus = (status: string) => {
    if (status === 'active') {
      return <span className="badge badge-success">Active</span>;
    }
    if (status === 'disabled') {
      return <span className="badge badge-warning">Disabled</span>;
    }
    if (status === 'rotated') {
      return <span className="badge badge-neutral">Rotated</span>;
    }
    return <span className="badge badge-error">{status}</span>;
  };

  return (
    <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="border-b border-slate-200 pb-4">
        <h3 className="text-lg font-semibold text-slate-900">Managed Secrets</h3>
        <p className="mt-2 max-w-3xl text-sm text-slate-600">
          Store encrypted secret values in MoonMind and reference them from provider
          profiles with values such as <code>db://OPENAI_API_KEY</code>.
        </p>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <section className="rounded-3xl border border-slate-200">
          <div className="border-b border-slate-200 px-5 py-4">
            <h4 className="text-base font-semibold text-slate-900">Stored secrets</h4>
          </div>
          <div className="overflow-x-auto px-5 py-4">
            <table
              className="table"
              style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse' }}
            >
              <thead>
                <tr style={{ borderBottom: '1px solid #ddd' }}>
                  <th style={{ padding: '8px' }}>Secret slug</th>
                  <th style={{ padding: '8px' }}>Status</th>
                  <th style={{ padding: '8px' }}>Updated</th>
                  <th style={{ padding: '8px' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {secrets.length === 0 ? (
                  <tr>
                    <td
                      colSpan={4}
                      style={{ padding: '8px', color: '#666', fontStyle: 'italic' }}
                    >
                      No secrets currently stored.
                    </td>
                  </tr>
                ) : (
                  secrets.map((secret) => (
                    <tr key={secret.slug} style={{ borderBottom: '1px solid #eee' }}>
                      <td
                        style={{
                          padding: '8px',
                          fontFamily: 'monospace',
                          fontWeight: 'bold',
                        }}
                      >
                        {secret.slug}
                      </td>
                      <td style={{ padding: '8px' }}>{renderStatus(secret.status)}</td>
                      <td style={{ padding: '8px', fontSize: '0.85em', color: '#666' }}>
                        {secret.updatedAt
                          ? new Date(secret.updatedAt).toLocaleString()
                          : new Date(secret.createdAt).toLocaleString()}
                      </td>
                      <td style={{ padding: '8px' }}>
                        <div style={{ display: 'flex', gap: '8px' }}>
                          <button
                            type="button"
                            onClick={() => {
                              setSlug(secret.slug);
                              setPlaintext('');
                              setIsEditing(true);
                            }}
                            className="btn btn-sm btn-outline"
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setRotatePromptSlug(secret.slug);
                              setRotatePromptVal('');
                              setRotatePromptOpen(true);
                            }}
                            className="btn btn-sm btn-outline"
                          >
                            Rotate
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Are you sure you want to completely delete ${secret.slug}?`,
                                )
                              ) {
                                deleteOp.mutate(secret.slug);
                              }
                            }}
                            className="btn btn-sm btn-error"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="rounded-3xl border border-slate-200">
          <div className="border-b border-slate-200 px-5 py-4">
            <h4 className="text-base font-semibold text-slate-900">
              {isEditing ? 'Update secret value' : 'Add new secret'}
            </h4>
          </div>
          <div className="px-5 py-4">
            <form className="stack" onSubmit={handleSubmit}>
              <div className="field">
                <label htmlFor="secSlug">Secret slug</label>
                <input
                  id="secSlug"
                  type="text"
                  placeholder="e.g. OPENAI_API_KEY"
                  value={slug}
                  onChange={(event) => setSlug(event.target.value)}
                  disabled={isEditing || createOp.isPending || updateOp.isPending}
                />
                <div className="field-hint">The unique locator used by the `db://` resolver.</div>
              </div>

              <div className="field">
                <label htmlFor="secPlaintext">Secure value</label>
                <input
                  id="secPlaintext"
                  type="password"
                  placeholder={isEditing ? '••••••••' : 'Enter raw secret string'}
                  value={plaintext}
                  onChange={(event) => setPlaintext(event.target.value)}
                  disabled={createOp.isPending || updateOp.isPending}
                />
                <div className="field-hint">
                  Stored strictly as ciphertext. The raw value is never rendered back.
                </div>
              </div>

              <div style={{ display: 'flex', gap: '8px', marginTop: '16px' }}>
                <button
                  type="submit"
                  className="settings-submit-btn"
                  disabled={createOp.isPending || updateOp.isPending}
                >
                  {isEditing ? 'Save new value' : 'Encrypt and store secret'}
                </button>

                {isEditing ? (
                  <button
                    type="button"
                    className="btn btn-outline"
                    onClick={() => {
                      setIsEditing(false);
                      setSlug('');
                      setPlaintext('');
                    }}
                  >
                    Cancel
                  </button>
                ) : null}
              </div>
            </form>
          </div>
        </section>
      </div>

      {rotatePromptOpen ? (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            className="card"
            style={{
              padding: '20px',
              maxWidth: '400px',
              width: '100%',
              background: 'var(--mm-bg-card, #fff)',
            }}
          >
            <h3>Rotate Secret: {rotatePromptSlug}</h3>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (rotatePromptVal) {
                  rotateOp.mutate({
                    slug: rotatePromptSlug,
                    plaintext: rotatePromptVal,
                  });
                  setRotatePromptOpen(false);
                }
              }}
              className="stack"
              style={{ marginTop: '16px' }}
            >
              <div className="field">
                <label>New secure value</label>
                <input
                  type="password"
                  value={rotatePromptVal}
                  onChange={(event) => setRotatePromptVal(event.target.value)}
                  autoFocus
                  required
                />
              </div>
              <div
                style={{
                  display: 'flex',
                  gap: '8px',
                  marginTop: '16px',
                  justifyContent: 'flex-end',
                }}
              >
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => setRotatePromptOpen(false)}
                >
                  Cancel
                </button>
                <button type="submit" className="settings-submit-btn">
                  Rotate now
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}
