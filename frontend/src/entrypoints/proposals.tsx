import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { mountPage } from '../boot/mountPage';
import { BootPayload } from '../boot/parseBootPayload';
import { executionStatusPillClasses } from '../utils/executionStatusPillClasses';
import { PageSizeSelector, parsePageSize } from '../components/PageSizeSelector';

const PROPOSAL_STATUSES = ['open', 'promoted', 'dismissed'] as const;

const TaskPreviewSchema = z
  .object({
    runtimeMode: z.string().nullable().optional(),
    skillId: z.string().nullable().optional(),
    taskSkills: z.array(z.string()).nullable().optional(),
  })
  .passthrough();

const ProposalSchema = z
  .object({
    id: z.string(),
    title: z.string(),
    summary: z.string().optional(),
    status: z.string(),
    category: z.string().nullable().optional(),
    repository: z.string(),
    createdAt: z.string(),
    promotedAt: z.string().nullable().optional(),
    taskPreview: TaskPreviewSchema.nullable().optional(),
  })
  .passthrough();

const ProposalsResponseSchema = z.object({
  items: z.array(ProposalSchema),
  nextCursor: z.string().nullable().optional(),
});

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function replaceUrlQuery(params: URLSearchParams) {
  const queryText = params.toString();
  const path = window.location.pathname;
  window.history.replaceState({}, '', queryText ? `${path}?${queryText}` : path);
}

function ProposalsPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const initial = useMemo(() => new URLSearchParams(window.location.search), []);

  const [status, setStatus] = useState(() => {
    const fromUrl = initial.get('status');
    if (fromUrl === null) return 'open';
    const normalized = fromUrl.toLowerCase();
    return normalized === '' || PROPOSAL_STATUSES.includes(normalized as (typeof PROPOSAL_STATUSES)[number]) ? normalized : 'open';
  });
  const [repository, setRepository] = useState(() => initial.get('repository') || '');
  const [debouncedRepository, setDebouncedRepository] = useState(repository);
  const [pageSize, setPageSize] = useState(() => parsePageSize(initial.get('limit')));
  const [listCursor, setListCursor] = useState<string | null>(() => initial.get('cursor')?.trim() || null);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  
  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedRepository(repository);
    }, 300);
    return () => clearTimeout(handler);
  }, [repository]);

  const normalizedRepository = debouncedRepository.trim();

  const syncUrl = useCallback(() => {
    const params = new URLSearchParams();
    if (status !== 'open') params.set('status', status);
    if (normalizedRepository) params.set('repository', normalizedRepository);
    params.set('limit', String(pageSize));
    if (listCursor) params.set('cursor', listCursor);
    replaceUrlQuery(params);
  }, [status, normalizedRepository, pageSize, listCursor]);

  useEffect(() => {
    syncUrl();
  }, [syncUrl]);

  const queryKey = ['proposals', pageSize, status, normalizedRepository, listCursor] as const;

  const promoteMutation = useMutation({
    mutationFn: async (id: string) => {
      const response = await fetch(`${payload.apiBase}/proposals/${id}/promote`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({}),
      });
      if (!response.ok) throw new Error('Failed to promote');
      return response.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['proposals'] }),
  });

  const dismissMutation = useMutation({
    mutationFn: async (id: string) => {
      const response = await fetch(`${payload.apiBase}/proposals/${id}/dismiss`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({}),
      });
      if (!response.ok) throw new Error('Failed to dismiss');
      return response.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['proposals'] }),
  });

  const { data, isLoading, isError, error } = useQuery({
    queryKey,
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set('limit', String(pageSize));
      if (listCursor) params.set('cursor', listCursor);
      if (status) params.set('status', status);
      if (normalizedRepository) params.set('repository', normalizedRepository);
      const response = await fetch(`${payload.apiBase}/proposals?${params}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return ProposalsResponseSchema.parse(await response.json());
    },
  });

  const sortedItems = data?.items || [];

  const pageIndex = cursorStack.length;
  const pageStart = sortedItems.length > 0 ? pageIndex * pageSize + 1 : 0;
  const pageEnd = pageIndex * pageSize + sortedItems.length;
  const hasPaginationContext = cursorStack.length > 0 || Boolean(listCursor);

  const resetToFirstPage = () => {
    setListCursor(null);
    setCursorStack([]);
  };

  const goNext = () => {
    const token = data?.nextCursor?.trim();
    if (!token) return;
    setCursorStack((stack) => [...stack, listCursor ?? '']);
    setListCursor(token);
  };

  const goPrev = () => {
    if (cursorStack.length === 0) return;
    const previousStack = cursorStack.slice(0, -1);
    const previousCursor = cursorStack[cursorStack.length - 1];
    setCursorStack(previousStack);
    setListCursor(previousCursor === undefined || previousCursor === '' ? null : previousCursor);
  };

  const exactPageKnown = cursorStack.length > 0 || !initial.get('cursor');

  const pageSummary = [
    exactPageKnown ? `Page ${pageIndex + 1}` : 'Continuing Page',
    pageEnd > 0 ? `${pageStart}-${pageEnd}` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h2 className="page-title">Proposals</h2>
          <p className="page-meta">Review and manage task proposals in the queue.</p>
        </div>
      </div>

      <form className="stack" onSubmit={(event) => event.preventDefault()}>
        <div className="grid-2">
          <label>
            Status
            <select
              value={status}
              onChange={(event) => {
                const raw = event.target.value;
                const nextStatus = raw === '' || PROPOSAL_STATUSES.includes(raw as (typeof PROPOSAL_STATUSES)[number]) ? raw : '';
                setStatus(nextStatus);
                resetToFirstPage();
              }}
            >
              <option value="">All States</option>
              {PROPOSAL_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            Repository
            <input
              type="text"
              value={repository}
              placeholder="owner/repo"
              onChange={(event) => {
                setRepository(event.target.value);
                resetToFirstPage();
              }}
            />
          </label>
        </div>
        <div className="grid-2">
          <div className="card">
            <strong>Status:</strong>{' '}
            {pageEnd > 0
              ? `Showing ${pageStart}-${pageEnd}`
              : 'No rows loaded.'}
          </div>
        </div>
      </form>

      {isLoading ? (
        <p className="loading">Loading proposals...</p>
      ) : isError ? (
        <div className="notice error">{(error as Error).message}</div>
      ) : sortedItems.length === 0 && !hasPaginationContext ? (
        <p className="small">No proposals found for the current filters.</p>
      ) : (
        <div className="queue-layouts">
          <div className="queue-results-toolbar">
            <span className="small">{pageSummary}</span>
            <div className="queue-pagination">
              <PageSizeSelector
                pageSize={pageSize}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  resetToFirstPage();
                }}
              />
              <nav aria-label="Pagination" style={{ display: 'inline-flex', gap: '0.45rem' }}>
                <button
                  type="button"
                  className="secondary queue-pagination-button"
                  disabled={cursorStack.length === 0}
                  onClick={goPrev}
                  aria-label="Previous page"
                >
                  <span aria-hidden="true">&larr;</span>
                </button>
                <button
                  type="button"
                  className="secondary queue-pagination-button"
                  disabled={!data?.nextCursor}
                  onClick={goNext}
                  aria-label="Next page"
                >
                  <span aria-hidden="true">&rarr;</span>
                </button>
              </nav>
            </div>
          </div>
          {sortedItems.length === 0 ? (
            <div className="card small">No proposals found for the current filters.</div>
          ) : (
            <>
              <div className="queue-table-wrapper" data-layout="table">
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Runtime</th>
                      <th>Skill</th>
                      <th>Repository</th>
                      <th>Status</th>
                      <th>Title</th>
                      <th>Created</th>
                      <th>Promoted</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedItems.map((row) => (
                      <tr key={row.id}>
                        <td>
                          <a href={`/proposals/${encodeURIComponent(row.id)}`}>
                            <code>{row.id.split('-').pop()}</code>
                          </a>
                        </td>
                        <td>{row.taskPreview?.runtimeMode || '—'}</td>
                        <td>{row.taskPreview?.taskSkills && row.taskPreview.taskSkills.length > 0 ? row.taskPreview.taskSkills.join(', ') : row.taskPreview?.skillId || '—'}</td>
                        <td>{row.repository || '—'}</td>
                        <td>
                          <span className={executionStatusPillClasses(row.status)}>
                            {row.status || '—'}
                          </span>
                        </td>
                        <td>{row.title}</td>
                        <td>{formatWhen(row.createdAt)}</td>
                        <td>{formatWhen(row.promotedAt)}</td>
                        <td>
                          {row.status === 'open' && (
                            <div style={{ display: 'flex', gap: '8px' }}>
                              <button
                                type="button"
                                className="button primary small"
                                onClick={() => promoteMutation.mutate(row.id)}
                                disabled={promoteMutation.isPending || dismissMutation.isPending}
                              >
                                Promote
                              </button>
                              <button
                                type="button"
                                className="button secondary small"
                                onClick={() => dismissMutation.mutate(row.id)}
                                disabled={promoteMutation.isPending || dismissMutation.isPending}
                              >
                                Dismiss
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="queue-card-list" data-layout="card" role="list">
                {sortedItems.map((row) => (
                  <li key={row.id} className="queue-card">
                    <div className="queue-card-header">
                      <div>
                        <a
                          href={`/proposals/${encodeURIComponent(row.id)}`}
                          className="queue-card-title"
                        >
                          {row.title}
                        </a>
                        <p className="queue-card-meta">
                          <code>{row.id}</code>
                          {` · ${
                            [row.taskPreview?.runtimeMode, row.taskPreview?.skillId].filter(Boolean).join(' · ') || 'Proposal'
                          }`}
                        </p>
                      </div>
                      <div className="queue-card-status">
                        <span className={executionStatusPillClasses(row.status)}>
                          {row.status || '—'}
                        </span>
                      </div>
                    </div>
                    <dl className="queue-card-fields">
                      <div>
                        <dt>ID</dt>
                        <dd>
                          <code>{row.id}</code>
                        </dd>
                      </div>
                      <div>
                        <dt>Runtime</dt>
                        <dd>{row.taskPreview?.runtimeMode || '—'}</dd>
                      </div>
                      <div>
                        <dt>Skill</dt>
                        <dd>{row.taskPreview?.taskSkills && row.taskPreview.taskSkills.length > 0 ? row.taskPreview.taskSkills.join(', ') : row.taskPreview?.skillId || '—'}</dd>
                      </div>
                      <div>
                        <dt>Repository</dt>
                        <dd>{row.repository || '—'}</dd>
                      </div>
                      <div>
                        <dt>Created</dt>
                        <dd>{formatWhen(row.createdAt)}</dd>
                      </div>
                      <div>
                        <dt>Promoted</dt>
                        <dd>{formatWhen(row.promotedAt)}</dd>
                      </div>
                    </dl>
                    <div className="queue-card-actions">
                      {row.status === 'open' && (
                        <>
                          <button
                            type="button"
                            className="button primary"
                            onClick={() => promoteMutation.mutate(row.id)}
                            disabled={promoteMutation.isPending || dismissMutation.isPending}
                          >
                            Promote
                          </button>
                          <button
                            type="button"
                            className="button secondary"
                            onClick={() => dismissMutation.mutate(row.id)}
                            disabled={promoteMutation.isPending || dismissMutation.isPending}
                          >
                            Dismiss
                          </button>
                        </>
                      )}
                      <a
                        href={`/proposals/${encodeURIComponent(row.id)}`}
                        className="button secondary"
                        role="button"
                      >
                        View details
                      </a>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}

mountPage(ProposalsPage);
