import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { Button } from '../components/dashboard/Button';
import { useDocumentTitle } from '../components/dashboard/PageHeader';
import { responseError } from '../lib/api/client';
import { formatBytes } from '../utils/formatters';
import { useUrlSearchText } from '../lib/urlSearchText';

type Category = 'artifacts' | 'reports' | 'observability';
type CollectionRow = {
  artifact_id: string;
  created_at: string;
  content_type?: string | null;
  size_bytes?: number | null;
  status: string;
  retention_class: string;
  link_type?: string | null;
  label?: string | null;
  workflow_id?: string | null;
  run_id?: string | null;
  download_url: string;
};
type CollectionResponse = {
  category: Category;
  items: CollectionRow[];
  has_more: boolean;
  offset: number;
  limit: number;
  refreshed_at: string;
};

const CATEGORIES: Array<{ value: Category; label: string; description: string }> = [
  { value: 'artifacts', label: 'Artifacts', description: 'Generated files and durable execution evidence.' },
  { value: 'reports', label: 'Reports', description: 'Published summaries, findings, and exports.' },
  { value: 'observability', label: 'Observability', description: 'Logs, diagnostics, and trace evidence.' },
];

function EvidenceSection({ category, apiBase }: { category: Category; apiBase: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const queryKey = `${category}_q`;
  const pageKey = `${category}_page`;
  const [query, setSearch] = useUrlSearchText(queryKey, [pageKey]);
  const page = Math.max(1, Number(params.get(pageKey) || '1') || 1);
  const limit = 25;
  const offset = (page - 1) * limit;
  const result = useQuery({
    queryKey: ['artifact-collection', category, query, offset],
    queryFn: async (): Promise<CollectionResponse> => {
      const search = new URLSearchParams({ category, offset: String(offset), limit: String(limit) });
      if (query) search.set('q', query);
      const response = await fetch(`${apiBase}/artifacts/collection?${search}`, { credentials: 'same-origin' });
      if (!response.ok) throw await responseError(response, `Collection request failed (${response.status})`);
      return response.json() as Promise<CollectionResponse>;
    },
  });
  const setPage = (next: number) => {
    const search = new URLSearchParams(location.search);
    if (next > 1) search.set(pageKey, String(next)); else search.delete(pageKey);
    navigate({ pathname: location.pathname, search: search.toString() });
  };
  const definition = CATEGORIES.find((item) => item.value === category)!;

  return (
    <section className="evidence-collection" aria-labelledby={`${category}-heading`}>
      <header className="evidence-collection__header">
        <div><h2 id={`${category}-heading`}>{definition.label}</h2><p>{definition.description}</p></div>
        <Button variant="secondary" onClick={() => void result.refetch()} disabled={result.isFetching}>Refresh</Button>
      </header>
      <label className="evidence-collection__filter">
        <span>Filter {definition.label.toLowerCase()}</span>
        <input value={query} onChange={(event) => setSearch(event.target.value)} type="search" />
      </label>
      {result.isPending ? <p role="status">Loading {definition.label.toLowerCase()}…</p> : null}
      {result.isError ? <div role="alert"><p>{result.error.message}</p><Button variant="secondary" onClick={() => void result.refetch()}>Try again</Button></div> : null}
      {result.data && result.data.items.length === 0 ? <p>{query ? `No ${definition.label.toLowerCase()} match this filter.` : `No authorized ${definition.label.toLowerCase()} are available.`}</p> : null}
      {result.data?.items.length ? (
        <div className="evidence-collection__table-wrap stacked-records-wrap"><table className="stacked-records"><thead><tr><th>Evidence</th><th>Provenance</th><th>Freshness</th><th>Size</th><th>Actions</th></tr></thead>
          <tbody>{result.data.items.map((row) => <tr key={row.artifact_id}>
            <td data-label="Evidence"><code>{row.artifact_id}</code><small>{row.content_type || 'Unknown type'} · {row.status}</small></td>
            <td data-label="Provenance">{row.workflow_id ? <Link to={`/workflows/${encodeURIComponent(row.workflow_id)}`}>{row.label || row.link_type || 'Workflow'}</Link> : (row.label || row.link_type || 'Unlinked')}</td>
            <td data-label="Freshness"><time dateTime={row.created_at}>{new Date(row.created_at).toLocaleString()}</time></td>
            <td data-label="Size">{formatBytes(row.size_bytes)}</td>
            <td data-label="Actions"><a href={row.download_url}>Download</a></td>
          </tr>)}</tbody></table></div>
      ) : null}
      {result.data ? <footer className="evidence-collection__pagination"><span>{result.data.items.length ? `Showing ${offset + 1}–${offset + result.data.items.length}` : `Page ${page}`} · refreshed <time dateTime={result.data.refreshed_at}>{new Date(result.data.refreshed_at).toLocaleTimeString()}</time></span><div><Button variant="secondary" size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</Button><Button variant="secondary" size="sm" disabled={!result.data.has_more} onClick={() => setPage(page + 1)}>Next</Button></div></footer> : null}
    </section>
  );
}

export default function ArtifactsPage({ payload }: { payload: BootPayload }) {
  const location = useLocation();
  const selected: Category = location.pathname === '/observability'
    || location.pathname.startsWith('/observability/')
    ? 'observability'
    : 'artifacts';
  useDocumentTitle('Artifacts & Observability');
  const enabled = payload.features?.artifacts !== false;
  if (!enabled) return <div className="evidence-workspace"><header><p className="eyebrow">Evidence</p><h1>Artifacts &amp; Observability</h1></header><div role="alert">Artifacts and observability are not enabled for this deployment.</div></div>;
  return <div className="evidence-workspace"><header><p className="eyebrow">Evidence</p><h1>Artifacts &amp; Observability</h1><p>Authorized evidence from MoonMind-owned, same-origin services.</p><nav aria-label="Evidence collections">{CATEGORIES.map((item) => <Link key={item.value} to={item.value === 'observability' ? '/observability' : `/artifacts#${item.value}-heading`}>{item.label}</Link>)}</nav></header><EvidenceSection category={selected} apiBase={payload.apiBase || '/api'} />{selected === 'artifacts' ? <><EvidenceSection category="reports" apiBase={payload.apiBase || '/api'} /><EvidenceSection category="observability" apiBase={payload.apiBase || '/api'} /></> : null}</div>;
}
