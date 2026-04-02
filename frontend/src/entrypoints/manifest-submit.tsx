import { useState } from 'react';

import { mountPage } from '../boot/mountPage';
import type { BootPayload } from '../boot/parseBootPayload';

function navigateTo(path: string): void {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function ManifestSubmitPage({ payload: _payload }: { payload: BootPayload }) {
  const [manifestName, setManifestName] = useState('');
  const [action, setAction] = useState('run');
  const [sourceKind, setSourceKind] = useState<'inline' | 'registry'>('inline');
  const [manifestContent, setManifestContent] = useState('');
  const [registryName, setRegistryName] = useState('');
  const [dryRun, setDryRun] = useState(false);
  const [forceFull, setForceFull] = useState(false);
  const [maxDocs, setMaxDocs] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    if (!manifestName.trim()) {
      setMessage('Manifest name is required.');
      return;
    }
    if (sourceKind === 'inline' && !manifestContent.trim()) {
      setMessage('Manifest YAML is required.');
      return;
    }
    if (sourceKind === 'registry' && !registryName.trim()) {
      setMessage('Registry name is required.');
      return;
    }

    setIsSubmitting(true);
    setMessage(null);
    const options: Record<string, boolean | number> = {};
    if (dryRun) {
      options.dryRun = true;
    }
    if (forceFull) {
      options.forceFull = true;
    }
    const parsedMaxDocs = Number(maxDocs);
    if (Number.isFinite(parsedMaxDocs) && parsedMaxDocs >= 1) {
      options.maxDocs = parsedMaxDocs;
    }

    try {
      const response = await fetch('/api/executions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          type: 'manifest',
          payload: {
            manifest: {
              name: manifestName.trim(),
              action,
              ...(Object.keys(options).length > 0 ? { options } : {}),
              source:
                sourceKind === 'registry'
                  ? { kind: 'registry', name: registryName.trim() }
                  : { kind: 'inline', content: manifestContent },
              content: manifestContent,
            },
          },
        }),
      });
      if (!response.ok) {
        throw new Error('Failed to create manifest job.');
      }
      const created = (await response.json()) as {
        workflowId?: string;
        redirectPath?: string;
      };
      const redirectPath =
        String(created.redirectPath || '').trim() ||
        (created.workflowId
          ? `/tasks/${encodeURIComponent(created.workflowId)}?source=temporal`
          : '');
      if (!redirectPath) {
        throw new Error('Manifest was created but no redirect path was returned.');
      }
      navigateTo(redirectPath);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Failed to create manifest job.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="space-y-6">
        <header className="rounded-[2rem] border border-mm-border/80 bg-transparent px-6 py-6 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
            Manifest Execution
          </p>
          <h2 className="mt-2 text-3xl font-semibold tracking-tight text-slate-950 dark:text-white">
            Submit Manifest
          </h2>
          <p className="mt-2 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Start a manifest-oriented Temporal run from the TypeScript Mission Control surface.
          </p>
        </header>

        <form onSubmit={handleSubmit}>
          <div className="grid gap-4 md:grid-cols-2">
            <label>
              Manifest Name
              <input
                value={manifestName}
                onChange={(event) => setManifestName(event.target.value)}
              />
            </label>

            <label>
              Action
              <select value={action} onChange={(event) => setAction(event.target.value)}>
                <option value="run">run</option>
                <option value="plan">plan</option>
              </select>
            </label>

            <label>
              Source Kind
              <select
                value={sourceKind}
                onChange={(event) => setSourceKind(event.target.value as 'inline' | 'registry')}
              >
                <option value="inline">inline</option>
                <option value="registry">registry</option>
              </select>
            </label>

            {sourceKind === 'registry' ? (
              <label>
                Registry Name
                <input
                  value={registryName}
                  onChange={(event) => setRegistryName(event.target.value)}
                />
              </label>
            ) : null}
          </div>

          {sourceKind === 'inline' ? (
            <label>
              Manifest Content
              <textarea
                value={manifestContent}
                onChange={(event) => setManifestContent(event.target.value)}
              />
            </label>
          ) : null}

          <div className="grid gap-4 md:grid-cols-3">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(event) => setDryRun(event.target.checked)}
              />
              Dry Run
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={forceFull}
                onChange={(event) => setForceFull(event.target.checked)}
              />
              Force Full
            </label>
            <label>
              Max Docs
              <input
                value={maxDocs}
                onChange={(event) => setMaxDocs(event.target.value)}
              />
            </label>
          </div>

          <div className="actions">
            <button type="submit" className="queue-submit-primary" disabled={isSubmitting}>
              {isSubmitting ? 'Submitting...' : 'Submit Manifest'}
            </button>
          </div>

          <p className={`queue-submit-message${message ? ' notice error' : ''}`}>
            {message || ''}
          </p>
        </form>
      </div>
    </div>
  );
}

mountPage(ManifestSubmitPage);
