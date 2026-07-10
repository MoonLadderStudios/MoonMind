import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardErrorState } from '../components/DashboardErrorState';
import { DataTable, type Column } from '../components/tables/DataTable';

type RemediationRow = {
  remediationWorkflowId: string;
  title: string;
  status: string;
  attentionRequired: boolean;
  targetWorkflowId: string;
  targetTitle: string;
  authorityMode: string;
  mode: string;
  latestActionSummary?: string | null;
  resolution?: string | null;
  createdAt: string;
  updatedAt: string;
};

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

async function loadRemediations(apiBase: string): Promise<RemediationRow[]> {
  const response = await fetch(`${apiBase}/executions/remediations`, { credentials: 'same-origin' });
  if (!response.ok) throw new Error(`Remediation inventory request failed: ${response.status}`);
  const payload = (await response.json()) as { items?: RemediationRow[] };
  return Array.isArray(payload.items) ? payload.items : [];
}

export default function Remediations({ payload }: { payload: BootPayload }) {
  const [filter, setFilter] = useState('');
  const query = useQuery({
    queryKey: ['remediations'],
    queryFn: () => loadRemediations(payload.apiBase || '/api'),
  });
  const rows = useMemo(() => {
    const term = filter.trim().toLowerCase();
    return term ? (query.data ?? []).filter((row) =>
      [row.title, row.status, row.targetTitle, row.latestActionSummary, row.resolution]
        .some((value) => String(value ?? '').toLowerCase().includes(term))) : (query.data ?? []);
  }, [filter, query.data]);
  const columns: Column<RemediationRow>[] = [
    { key: 'title', header: 'Remediation', render: (row) => <Link to={`/workflows/${encodeURIComponent(row.remediationWorkflowId)}`}>{row.title}</Link> },
    { key: 'status', header: 'Lifecycle / attention', render: (row) => <><span>{row.status}</span>{row.attentionRequired ? <strong> · Attention</strong> : null}</> },
    { key: 'targetTitle', header: 'Source Workflow', render: (row) => <Link to={`/workflows/${encodeURIComponent(row.targetWorkflowId)}`}>{row.targetTitle}</Link> },
    { key: 'authorityMode', header: 'Contract', render: (row) => `${row.authorityMode} · ${row.mode}` },
    { key: 'latestActionSummary', header: 'Latest action', render: (row) => row.latestActionSummary || row.resolution || '—' },
    { key: 'updatedAt', header: 'Updated', sortable: true, render: (row) => formatDateTime(row.updatedAt) },
  ];
  if (query.isError) return <DashboardErrorState title="Remediation inventory unavailable" description="MoonMind could not load remediations." detail={query.error instanceof Error ? query.error.message : null} onRetry={() => void query.refetch()} />;
  return <main className="data-wide-panel" aria-labelledby="remediations-title">
    <div className="page-header"><div><h2 id="remediations-title">Remediation</h2><p>Scan remediation workflows and their source Workflow provenance.</p></div><button type="button" onClick={() => void query.refetch()}>Refresh</button></div>
    <label htmlFor="remediation-filter">Filter remediations</label>
    <input id="remediation-filter" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Title, status, source, or action" />
    <DataTable data={rows} columns={columns} getRowKey={(row) => row.remediationWorkflowId} ariaLabel="Remediation workflows" isLoading={query.isPending} emptyMessage={filter ? 'No remediations match the current filter.' : 'No remediation workflows are visible.'} responsive />
  </main>;
}
