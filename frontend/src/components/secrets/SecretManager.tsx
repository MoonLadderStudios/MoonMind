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
    <div className="rounded-3xl border border-mm-border/80 bg-mm-panel/75 p-6 shadow-sm">
      <div className="border-b border-slate-200 dark:border-slate-800 pb-4">
        <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Managed Secrets</h3>
        <p className="mt-2 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
          Store encrypted secret values in MoonMind and reference them from provider
          profiles with values such as <code>db://OPENAI_API_KEY</code>.
        </p>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <section className="rounded-3xl border border-slate-200 dark:border-slate-800">
          <div className="border-b border-slate-200 dark:border-slate-800 px-5 py-4">
            <h4 className="text-base font-semibold text-slate-900 dark:text-white">Stored secrets</h4>
          </div>
          <div className="overflow-x-auto px-5 py-4">
            <table className="w-full text-left text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-200 dark:border-slate-800">
                  <th className="px-2 py-3 font-medium text-slate-600 dark:text-slate-400">Secret slug</th>
                  <th className="px-2 py-3 font-medium text-slate-600 dark:text-slate-400">Status</th>
                  <th className="px-2 py-3 font-medium text-slate-600 dark:text-slate-400">Updated</th>
                  <th className="px-2 py-3 font-medium text-slate-600 dark:text-slate-400">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {secrets.length === 0 ? (
                  <tr>
                    <td className="px-2 py-6 text-slate-500 dark:text-slate-400 italic" colSpan={4}>
                      No secrets currently stored.
                    </td>
                  </tr>
                ) : (
                  secrets.map((secret) => (
                    <tr key={secret.slug}>
                      <td className="px-2 py-4 font-mono font-bold text-slate-900 dark:text-white">
                        {secret.slug}
                      </td>
                      <td className="px-2 py-4">{renderStatus(secret.status)}</td>
                      <td className="px-2 py-4 text-xs text-slate-500 dark:text-slate-400">
                        {secret.updatedAt
                          ? new Date(secret.updatedAt).toLocaleString()
                          : new Date(secret.createdAt).toLocaleString()}
                      </td>
                      <td className="px-2 py-4">
                        <div className="flex gap-2">
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
                            className="queue-action queue-action-danger px-3 py-1 text-xs"
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

        <section className="rounded-3xl border border-slate-200 dark:border-slate-800">
          <div className="border-b border-slate-200 dark:border-slate-800 px-5 py-4">
            <h4 className="text-base font-semibold text-slate-900 dark:text-white">
              {isEditing ? 'Update secret value' : 'Add new secret'}
            </h4>
          </div>
          <div className="px-5 py-4">
            <form className="stack" onSubmit={handleSubmit}>
              <div className="field">
                <label htmlFor="secSlug" className="text-slate-700 dark:text-slate-300">Secret slug</label>
                <input
                  id="secSlug"
                  type="text"
                  placeholder="e.g. OPENAI_API_KEY"
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={slug}
                  onChange={(event) => setSlug(event.target.value)}
                  disabled={isEditing || createOp.isPending || updateOp.isPending}
                />
                <div className="field-hint text-slate-500 dark:text-slate-400">The unique locator used by the `db://` resolver.</div>
              </div>

              <div className="field mt-4">
                <label htmlFor="secPlaintext" className="text-slate-700 dark:text-slate-300">Secure value</label>
                <input
                  id="secPlaintext"
                  type="password"
                  placeholder={isEditing ? '••••••••' : 'Enter raw secret string'}
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={plaintext}
                  onChange={(event) => setPlaintext(event.target.value)}
                  disabled={createOp.isPending || updateOp.isPending}
                />
                <div className="field-hint text-slate-500 dark:text-slate-400">
                  Stored strictly as ciphertext. The raw value is never rendered back.
                </div>
              </div>

              <div className="flex gap-2 mt-6">
                <button
                  type="submit"
                  className="settings-submit-btn inline-flex items-center justify-center rounded-full bg-slate-900 dark:bg-slate-100 px-5 py-2.5 text-sm font-semibold text-white dark:text-slate-900 transition hover:bg-slate-800 dark:hover:bg-slate-200"
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
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-[1000]">
          <div className="bg-mm-panel/90 p-6 rounded-3xl shadow-2xl border border-mm-border/80 max-w-md w-full mx-4">
            <h3 className="text-xl font-semibold text-slate-900 dark:text-white">Rotate Secret: {rotatePromptSlug}</h3>
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
              className="mt-6 space-y-4"
            >
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">New secure value</label>
                <input
                  type="password"
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={rotatePromptVal}
                  onChange={(event) => setRotatePromptVal(event.target.value)}
                  autoFocus
                  required
                />
              </div>
              <div className="flex justify-end gap-3 mt-8">
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => setRotatePromptOpen(false)}
                >
                  Cancel
                </button>
                <button type="submit" className="settings-submit-btn inline-flex items-center justify-center rounded-full bg-slate-900 dark:bg-slate-100 px-5 py-2.5 text-sm font-semibold text-white dark:text-slate-900 transition hover:bg-slate-800 dark:hover:bg-slate-200">
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
