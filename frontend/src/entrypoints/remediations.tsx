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

type RemediationEndpointConfig = {
  enabled: boolean;
  endpoint: string | null;
};

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

function compactText(record: Record<string, unknown>, key: keyof RemediationRow): string {
  const value = record[key];
  return typeof value === 'string' ? value.trim() : '';
}

function isWorkflowId(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:{}-]{0,254}$/.test(value);
}

function compactRows(payload: unknown): RemediationRow[] {
  const items = payload && typeof payload === 'object' && Array.isArray((payload as { items?: unknown }).items)
    ? (payload as { items: unknown[] }).items
    : [];
  return items.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
    const record = item as Record<string, unknown>;
    const remediationWorkflowId = compactText(record, 'remediationWorkflowId');
    const targetWorkflowId = compactText(record, 'targetWorkflowId');
    if (!isWorkflowId(remediationWorkflowId) || !isWorkflowId(targetWorkflowId)) return [];
    return [{
      remediationWorkflowId,
      targetWorkflowId,
      title: compactText(record, 'title') || remediationWorkflowId,
      status: compactText(record, 'status') || 'unknown',
      attentionRequired: record.attentionRequired === true,
      targetTitle: compactText(record, 'targetTitle') || targetWorkflowId,
      authorityMode: compactText(record, 'authorityMode') || 'unspecified',
      mode: compactText(record, 'mode') || 'unspecified',
      latestActionSummary: compactText(record, 'latestActionSummary') || null,
      resolution: compactText(record, 'resolution') || null,
      createdAt: compactText(record, 'createdAt'),
      updatedAt: compactText(record, 'updatedAt'),
    }];
  });
}

function isSameOriginPath(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//');
}

function remediationEndpointConfig(payload: BootPayload): RemediationEndpointConfig {
  const initialData = payload.initialData as { uiEndpoints?: Record<string, unknown> } | undefined;
  const endpoint = initialData?.uiEndpoints?.remediations;
  return {
    enabled: payload.features?.remediationCollection === true && isSameOriginPath(endpoint),
    endpoint: isSameOriginPath(endpoint) ? endpoint : null,
  };
}

async function loadRemediations(endpoint: string): Promise<RemediationRow[]> {
  const response = await fetch(endpoint, { credentials: 'same-origin' });
  if (!response.ok) throw new Error(`Remediation inventory request failed: ${response.status}`);
  return compactRows(await response.json());
}

export default function Remediations({ payload }: { payload: BootPayload }) {
  const [filter, setFilter] = useState('');
  const endpointConfig = remediationEndpointConfig(payload);
  const query = useQuery({
    queryKey: ['remediations'],
    enabled: endpointConfig.enabled,
    queryFn: () => loadRemediations(endpointConfig.endpoint!),
  });
  const rows = useMemo(() => {
    const term = filter.trim().toLowerCase();
    return term ? (query.data ?? []).filter((row) =>
      [row.title, row.status, row.targetTitle, row.targetWorkflowId, row.latestActionSummary, row.resolution]
        .some((value) => String(value ?? '').toLowerCase().includes(term))) : (query.data ?? []);
  }, [filter, query.data]);
  const columns = useMemo<Column<RemediationRow>[]>(() => [
    { key: 'title', header: 'Remediation', render: (row) => <Link to={`/workflows/${encodeURIComponent(row.remediationWorkflowId)}`} aria-label={`${row.title} remediation workflow`}>{row.title}</Link> },
    { key: 'status', header: 'Lifecycle / attention', render: (row) => <><span>{row.status}</span>{row.attentionRequired ? <strong> · Attention</strong> : null}</> },
    { key: 'targetTitle', header: 'Source Workflow', render: (row) => <Link to={`/workflows/${encodeURIComponent(row.targetWorkflowId)}`} aria-label={`${row.targetTitle} source workflow`}>{row.targetTitle}</Link> },
    { key: 'authorityMode', header: 'Contract', render: (row) => `${row.authorityMode} · ${row.mode}` },
    { key: 'latestActionSummary', header: 'Latest action', render: (row) => row.latestActionSummary || row.resolution || '—' },
    { key: 'createdAt', header: 'Created', sortable: true, render: (row) => formatDateTime(row.createdAt) },
    { key: 'updatedAt', header: 'Updated', sortable: true, render: (row) => formatDateTime(row.updatedAt) },
  ], []);
  if (!endpointConfig.enabled) {
    return <main className="data-wide-panel" aria-labelledby="remediations-title"><div role="alert"><h2 id="remediations-title">Remediation</h2><p>Remediation inventory is not enabled for this deployment.</p></div></main>;
  }
  if (query.isError) return <DashboardErrorState title="Remediation inventory unavailable" description="MoonMind could not load remediations." detail={query.error instanceof Error ? query.error.message : null} onRetry={() => void query.refetch()} />;
  return <main className="data-wide-panel" aria-labelledby="remediations-title">
    <div className="page-header"><div><h2 id="remediations-title">Remediation</h2><p>Scan remediation workflows and their source Workflow provenance.</p></div><button type="button" onClick={() => void query.refetch()}>Refresh</button></div>
    <label htmlFor="remediation-filter">Filter remediations</label>
    <input id="remediation-filter" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Title, status, source, or action" />
    <DataTable data={rows} columns={columns} getRowKey={(row) => row.remediationWorkflowId} ariaLabel="Remediation workflows" isLoading={query.isPending} emptyMessage={filter ? 'No remediations match the current filter.' : 'No remediation workflows are visible.'} responsive />
  </main>;
}
