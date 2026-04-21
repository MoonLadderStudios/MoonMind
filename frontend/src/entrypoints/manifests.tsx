import { DataTable } from '../components/tables/DataTable';
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';
import { useMemo, useState, type FormEvent } from 'react';

const ManifestRunSchema = z
  .object({
    taskId: z.string(),
    source: z.string(),
    sourceLabel: z.string().optional(),
    title: z.string().nullable().optional(),
    manifestName: z.string().nullable().optional(),
    action: z.string().nullable().optional(),
    status: z.string(),
    state: z.string().nullable().optional(),
    rawState: z.string().nullable().optional(),
    temporalStatus: z.string().nullable().optional(),
    currentStage: z.string().nullable().optional(),
    manifestStage: z.string().nullable().optional(),
    stage: z.string().nullable().optional(),
    startedAt: z.string().nullable().optional(),
    createdAt: z.string().nullable().optional(),
    durationSeconds: z.number().nullable().optional(),
    detailHref: z.string().nullable().optional(),
    link: z.string().nullable().optional(),
  })
  .passthrough();
type ManifestRun = z.infer<typeof ManifestRunSchema>;
type SourceKind = 'inline' | 'registry';
type Notice = {
  level: 'ok' | 'error';
  text: string;
  href?: string;
};

const RAW_SECRET_PATTERNS = [
  /\bghp_[A-Za-z0-9_]{20,}\b/i,
  /\bgithub_pat_[A-Za-z0-9_]{20,}\b/i,
  /\bAIza[A-Za-z0-9_-]{20,}\b/,
  /\bATATT[A-Za-z0-9_-]{10,}\b/,
  /\bAKIA[A-Z0-9]{16}\b/,
  /-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH)\s+PRIVATE\s+KEY-----/i,
  /\b(?:token|password|secret)\s*[:=]\s*(?!\s*(?:\$\{|env:\/\/|vault:\/\/))[^\s'",}]+/i,
];

const ManifestsResponseSchema = z.object({
  items: z.array(ManifestRunSchema),
});

function hasRawSecretLikeValue(values: string[]): boolean {
  return values.some((value) => RAW_SECRET_PATTERNS.some((pattern) => pattern.test(value)));
}

function displayValue(value: string | null | undefined): string {
  const normalized = String(value || '').trim();
  return normalized || '-';
}

function manifestLabel(run: ManifestRun): string {
  return displayValue(run.manifestName || run.sourceLabel || run.title || run.source || run.taskId);
}

function runAction(run: ManifestRun): string {
  return displayValue(run.action);
}

function runStage(run: ManifestRun): string {
  return String(run.currentStage || run.manifestStage || run.stage || '').trim();
}

function statusLabel(run: ManifestRun): string {
  const status = displayValue(run.status || run.rawState || run.state || run.temporalStatus);
  const stage = runStage(run);
  if (!stage) {
    return status;
  }
  const activeStatuses = new Set(['running', 'executing', 'in progress', 'processing']);
  return activeStatuses.has(status.toLowerCase()) ? `${status[0].toUpperCase()}${status.slice(1)} · ${stage}` : status;
}

function detailHref(run: ManifestRun): string {
  const direct = String(run.detailHref || run.link || '').trim();
  return direct || `/tasks/${encodeURIComponent(run.taskId)}?source=temporal`;
}

function formatWhen(value: string | null | undefined): string {
  const raw = String(value || '').trim();
  if (!raw) {
    return '-';
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  return date.toLocaleString();
}

function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) {
    return '-';
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function matchesText(haystack: string[], needle: string): boolean {
  const normalized = needle.trim().toLowerCase();
  if (!normalized) {
    return true;
  }
  return haystack.some((value) => value.toLowerCase().includes(normalized));
}

export function ManifestsPage({ payload }: { payload: BootPayload }) {
  const [manifestName, setManifestName] = useState('');
  const [action, setAction] = useState('run');
  const [sourceKind, setSourceKind] = useState<SourceKind>('registry');
  const [manifestContent, setManifestContent] = useState('');
  const [registryName, setRegistryName] = useState('');
  const [dryRun, setDryRun] = useState(false);
  const [forceFull, setForceFull] = useState(false);
  const [maxDocs, setMaxDocs] = useState('');
  const [notice, setNotice] = useState<Notice | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [statusFilter, setStatusFilter] = useState('all');
  const [manifestFilter, setManifestFilter] = useState('');
  const [searchFilter, setSearchFilter] = useState('');

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

  const runs = data?.items || [];
  const statusOptions = useMemo(() => {
    return Array.from(new Set(runs.map((run) => run.status).filter(Boolean))).sort((left, right) =>
      left.localeCompare(right),
    );
  }, [runs]);
  const filteredRuns = useMemo(() => {
    return runs.filter((run) => {
      if (statusFilter !== 'all' && run.status !== statusFilter) {
        return false;
      }
      if (!matchesText([manifestLabel(run)], manifestFilter)) {
        return false;
      }
      return matchesText(
        [run.taskId, manifestLabel(run), runAction(run), statusLabel(run), runStage(run)],
        searchFilter,
      );
    });
  }, [manifestFilter, runs, searchFilter, statusFilter]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }

    const trimmedManifestName = manifestName.trim();
    const trimmedRegistryName = registryName.trim();
    const manifestKey = sourceKind === 'registry' ? trimmedRegistryName : trimmedManifestName;

    if (!manifestKey) {
      setNotice({
        level: 'error',
        text: sourceKind === 'registry' ? 'Registry manifest name is required.' : 'Manifest name is required.',
      });
      return;
    }
    if (sourceKind === 'inline' && !manifestContent.trim()) {
      setNotice({ level: 'error', text: 'Manifest YAML is required.' });
      return;
    }
    const secretCheckValues =
      sourceKind === 'registry' ? [trimmedRegistryName] : [trimmedManifestName, manifestContent];
    if (hasRawSecretLikeValue(secretCheckValues)) {
      setNotice({
        level: 'error',
        text: 'Raw secret-like values are not allowed. Use env or Vault references instead.',
      });
      return;
    }
    const trimmedMaxDocs = maxDocs.trim();
    let parsedMaxDocs: number | undefined;
    if (trimmedMaxDocs) {
      const maxDocsCandidate = Number(trimmedMaxDocs);
      if (
        !/^[1-9]\d*$/.test(trimmedMaxDocs) ||
        !Number.isSafeInteger(maxDocsCandidate) ||
        maxDocsCandidate <= 0
      ) {
        setNotice({ level: 'error', text: 'Max Docs must be a positive whole number.' });
        return;
      }
      parsedMaxDocs = maxDocsCandidate;
    }

    setIsSubmitting(true);
    setNotice(null);
    const options: Record<string, boolean | number> = {};
    if (dryRun) {
      options.dryRun = true;
    }
    if (forceFull) {
      options.forceFull = true;
    }
    if (parsedMaxDocs !== undefined) {
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
            title: sourceKind === 'registry' ? manifestKey : trimmedManifestName,
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
          link?: string;
        };
      };
      const runId = String(created.execution?.workflowId || '').trim();
      const runLink = String(created.execution?.link || '').trim();
      const successNotice: Notice = {
        level: 'ok',
        text: runId ? `Manifest run started: ${runId}` : 'Manifest run started.',
      };
      if (runLink || runId) {
        successNotice.href = runLink || `/tasks/${encodeURIComponent(runId)}`;
      }
      setNotice({
        ...successNotice,
      });
      await refetch();
    } catch (error) {
      setNotice({
        level: 'error',
        text: error instanceof Error ? error.message : 'Failed to create manifest run.',
      });
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

          <p className={`queue-submit-message${notice ? ` notice ${notice.level}` : ''}`}>
            {notice ? (
              notice.href ? (
                <>
                  {notice.text}{' '}
                  <a href={notice.href}>Open run</a>
                </>
              ) : (
                notice.text
              )
            ) : (
              ''
            )}
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
          <>
            <div className="grid gap-4 md:grid-cols-3 mb-4">
              <label>
                Filter by status
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                  <option value="all">All statuses</option>
                  {statusOptions.map((status) => (
                    <option key={status} value={status}>
                      {status}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Filter by manifest
                <input
                  value={manifestFilter}
                  onChange={(event) => setManifestFilter(event.target.value)}
                  placeholder="Manifest name"
                />
              </label>
              <label>
                Search recent runs
                <input
                  value={searchFilter}
                  onChange={(event) => setSearchFilter(event.target.value)}
                  placeholder="Run ID, action, status, or stage"
                />
              </label>
            </div>
            <DataTable
              ariaLabel="Recent manifest runs"
              data={filteredRuns}
              columns={[
                {
                  key: 'taskId',
                  header: 'Run ID',
                  render: (item: ManifestRun) => (
                    <a href={detailHref(item)} aria-label={`Open run ${item.taskId}`}>
                      <code>{item.taskId}</code>
                    </a>
                  ),
                },
                { key: 'manifestName', header: 'Manifest', render: manifestLabel },
                { key: 'action', header: 'Action', render: runAction },
                { key: 'status', header: 'Status', render: statusLabel },
                {
                  key: 'startedAt',
                  header: 'Started',
                  render: (item: ManifestRun) => formatWhen(item.startedAt || item.createdAt),
                },
                {
                  key: 'durationSeconds',
                  header: 'Duration',
                  render: (item: ManifestRun) => formatDuration(item.durationSeconds),
                },
                {
                  key: 'actions',
                  header: 'Actions',
                  render: (item: ManifestRun) => (
                    <a href={detailHref(item)} aria-label={`View details for ${item.taskId}`}>
                      View details
                    </a>
                  ),
                },
              ]}
              emptyMessage="No manifest runs exist yet. Run a registry manifest or submit inline YAML above."
              getRowKey={(item) => item.taskId}
            />
          </>
        )}
      </div>
    </div>
  );
}
export default ManifestsPage;
