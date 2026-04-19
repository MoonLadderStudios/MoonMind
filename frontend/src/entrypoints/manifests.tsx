import { DataTable } from '../components/tables/DataTable';
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';
import { useState, type FormEvent } from 'react';

const ManifestRunSchema = z.object({
  taskId: z.string(),
  source: z.string(),
  sourceLabel: z.string().optional(),
  status: z.string(),
});
type ManifestRun = z.infer<typeof ManifestRunSchema>;
type SourceKind = 'inline' | 'registry';

const ManifestsResponseSchema = z.object({
  items: z.array(ManifestRunSchema),
});

export function ManifestsPage({ payload }: { payload: BootPayload }) {
  const [manifestName, setManifestName] = useState('');
  const [action, setAction] = useState('run');
  const [sourceKind, setSourceKind] = useState<SourceKind>('registry');
  const [manifestContent, setManifestContent] = useState('');
  const [registryName, setRegistryName] = useState('');
  const [dryRun, setDryRun] = useState(false);
  const [forceFull, setForceFull] = useState(false);
  const [maxDocs, setMaxDocs] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['manifests'],
    queryFn: async () => {
      const response = await fetch(`${payload.apiBase}/executions?entry=manifest&limit=200`);
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return ManifestsResponseSchema.parse(await response.json());
    },
  });

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }

    const trimmedManifestName = manifestName.trim();
    const trimmedRegistryName = registryName.trim();
    const manifestKey = sourceKind === 'registry' ? trimmedRegistryName : trimmedManifestName;

    if (!manifestKey) {
      setMessage(
        sourceKind === 'registry' ? 'Registry manifest name is required.' : 'Manifest name is required.',
      );
      return;
    }
    if (sourceKind === 'inline' && !manifestContent.trim()) {
      setMessage('Manifest YAML is required.');
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
      if (sourceKind === 'inline') {
        const upsertResponse = await fetch(
          `${payload.apiBase}/manifests/${encodeURIComponent(manifestKey)}`,
          {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/json',
              Accept: 'application/json',
            },
            body: JSON.stringify({
              content: manifestContent,
            }),
          },
        );
        if (!upsertResponse.ok) {
          throw new Error('Failed to save manifest.');
        }
      }

      const response = await fetch(
        `${payload.apiBase}/manifests/${encodeURIComponent(manifestKey)}/runs`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
          },
          body: JSON.stringify({
            action,
            title: trimmedManifestName || manifestKey,
            ...(Object.keys(options).length > 0 ? { options } : {}),
          }),
        },
      );
      if (!response.ok) {
        throw new Error('Failed to create manifest run.');
      }
      const created = (await response.json()) as {
        execution?: {
          workflowId?: string;
        };
      };
      const runId = String(created.execution?.workflowId || '').trim();
      setMessage(runId ? `Manifest run started: ${runId}` : 'Manifest run started.');
      await refetch();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Failed to create manifest run.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <header className="border-b border-gray-200 pb-4 mb-6">
        <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Manifests</h2>
        <p className="text-sm text-gray-500 mt-1">Run a manifest and monitor recent executions in one place.</p>
      </header>

      <div className="bg-white rounded-lg shadow-sm p-6 border border-gray-100">
        <div className="mb-4">
          <h3 className="text-lg font-semibold text-gray-900">Run Manifest</h3>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <label>
            Source Kind
            <select
              value={sourceKind}
              onChange={(event) => setSourceKind(event.target.value as SourceKind)}
            >
              <option value="registry">Registry Manifest</option>
              <option value="inline">Inline YAML</option>
            </select>
          </label>

          {sourceKind === 'registry' ? (
            <label>
              Registry Manifest Name
              <input
                value={registryName}
                onChange={(event) => setRegistryName(event.target.value)}
              />
            </label>
          ) : (
            <>
              <label>
                Manifest Name
                <input
                  value={manifestName}
                  onChange={(event) => setManifestName(event.target.value)}
                />
              </label>
              <label>
                Inline YAML
                <textarea
                  value={manifestContent}
                  onChange={(event) => setManifestContent(event.target.value)}
                />
              </label>
            </>
          )}

          <label>
            Action
            <select value={action} onChange={(event) => setAction(event.target.value)}>
              <option value="run">run</option>
              <option value="plan">plan</option>
            </select>
          </label>

          <details>
            <summary>Advanced options</summary>
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
          </details>

          <div className="actions">
            <button type="submit" className="queue-submit-primary" disabled={isSubmitting}>
              {isSubmitting ? 'Running...' : 'Run Manifest'}
            </button>
          </div>

          <p className={`queue-submit-message${message ? ' notice' : ''}`}>
            {message || ''}
          </p>
        </form>
      </div>

      <div className="bg-white rounded-lg shadow-sm p-6 border border-gray-100">
        <div className="mb-4">
          <h3 className="text-lg font-semibold text-gray-900">Recent Runs</h3>
        </div>
        {isLoading ? (
          <p className="text-gray-500 italic animate-pulse">Loading manifest jobs...</p>
        ) : isError ? (
          <div className="p-4 rounded-md bg-red-50 text-red-700 border border-red-200 mb-4">{(error as Error).message}</div>
        ) : (
          <DataTable
            data={data?.items || []}
            columns={[
              { key: 'taskId', header: 'Task ID' },
              { key: 'sourceLabel', header: 'Source Label', render: (item: ManifestRun) => item.sourceLabel || item.source },
              { key: 'status', header: 'Status' },
            ]}
            emptyMessage="No manifest runs found."
            getRowKey={(item) => item.taskId}
          />
        )}
      </div>
    </div>
  );
}
export default ManifestsPage;
