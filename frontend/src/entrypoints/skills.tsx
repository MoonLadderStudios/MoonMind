import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { marked } from 'marked';

import type { BootPayload } from '../boot/parseBootPayload';
import { LoadingPlaceholder } from '../components/dashboard/LoadingPlaceholder';
import {
  CollectionColumnFilter,
  CollectionSidebar,
  CollectionSidebarFilterHeader,
  type CollectionSidebarFilterCopy,
  type CollectionSidebarRow,
} from '../components/CollectionSidebar';
import { CollectionWorkspace } from '../components/CollectionWorkspace';
import { DataTable, type Column } from '../components/tables/DataTable';
import {
  decodeSkillDetail,
  isCollectionListDisplayMode,
  type CollectionListDisplayMode,
} from '../lib/collectionListDisplayMode';
import { updateDashboardPreferences } from '../utils/dashboardPreferences';

/**
 * Normalized Skills catalog item shared by the full table, the sidebar, and
 * the detail pane. The catalog response is normalized exactly once so every
 * surface renders the same metadata.
 */
export interface SkillCatalogItem {
  id: string;
  label: string | null;
  description: string | null;
  markdown: string | null;
  hasInputSchema: boolean;
  sourceKind: string | null;
}

type PreviewTab = 'rendered' | 'raw' | 'metadata';

type CollisionPolicy = 'reject';

// Collision policy options surfaced for zip uploads. The values map directly to
// the `collision_policy` field accepted by POST /api/skills/imports. Only
// `reject` is exposed today: the backend returns 409 for `new_version` until
// versioned skill storage exists, so surfacing that option would advertise a
// recovery path that always fails.
const COLLISION_POLICIES: Array<{ value: CollisionPolicy; label: string; description: string }> = [
  {
    value: 'reject',
    label: 'Reject on collision',
    description: 'Fail the upload if a skill with the same name already exists.',
  },
];

// Skill names map onto runtime-visible skill folders, so keep them to a safe,
// filesystem-friendly slug. Validation runs before submit.
const SKILL_NAME_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/;

const SKILL_SIDEBAR_FILTER_COPY: CollectionSidebarFilterCopy = {
  columnHeader: 'Skill',
  dialogLabel: 'Skill sidebar filter',
  dialogTitle: 'Skill filter',
  fieldLabel: 'Skill',
  placeholder: 'Filter skills',
  inputLabel: 'Skill sidebar filter value',
  triggerIdleLabel: 'Skill sidebar filter. No filter applied.',
  triggerActiveLabel: (value) => `Skill sidebar filter: ${value}`,
  resetLabel: 'Reset skill sidebar filter',
  applyLabel: 'Apply skill sidebar filter',
};

function validateSkillName(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return 'Skill name is required.';
  }
  if (!SKILL_NAME_PATTERN.test(trimmed)) {
    return 'Skill name may only contain letters, numbers, dots, dashes, and underscores.';
  }
  return null;
}

function normalizeSkillCatalog(payload: unknown): SkillCatalogItem[] {
  const raw = payload && typeof payload === 'object' && !Array.isArray(payload)
    ? (payload as { legacyItems?: unknown }).legacyItems
    : null;
  if (!Array.isArray(raw)) {
    return [];
  }
  const normalized: SkillCatalogItem[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      continue;
    }
    const record = entry as Record<string, unknown>;
    const id = typeof record.id === 'string' ? record.id.trim() : '';
    if (!id) {
      continue;
    }
    const source = record.source && typeof record.source === 'object' && !Array.isArray(record.source)
      ? (record.source as Record<string, unknown>)
      : null;
    const label = typeof record.label === 'string' && record.label.trim() ? record.label.trim() : null;
    const description = typeof record.description === 'string' && record.description.trim()
      ? record.description.trim()
      : null;
    normalized.push({
      id,
      label,
      description,
      markdown: typeof record.markdown === 'string' ? record.markdown : null,
      hasInputSchema: record.hasInputSchema === true,
      sourceKind: source && typeof source.kind === 'string' && source.kind.trim()
        ? source.kind.trim()
        : null,
    });
  }
  return normalized;
}

function skillMatchesFilter(item: SkillCatalogItem, filterText: string): boolean {
  const normalized = filterText.trim().toLocaleLowerCase();
  if (!normalized) {
    return true;
  }
  return (
    item.id.toLocaleLowerCase().includes(normalized)
    || (item.label ?? '').toLocaleLowerCase().includes(normalized)
  );
}

function skillDetailHref(skillId: string): string {
  return `/skills/${encodeURIComponent(skillId)}`;
}

function skillSourceLabel(sourceKind: string | null): string {
  if (!sourceKind) {
    return '—';
  }
  const humanized = sourceKind.replace(/[_-]+/g, ' ').trim();
  return humanized ? humanized.charAt(0).toUpperCase() + humanized.slice(1) : '—';
}

function skillContentSummary(item: SkillCatalogItem): string {
  if (!item.markdown || !item.markdown.trim()) {
    return '—';
  }
  const lines = item.markdown.split('\n').length;
  return `${lines} ${lines === 1 ? 'line' : 'lines'}`;
}

function skillsBootData(payload: BootPayload): Record<string, unknown> {
  const raw = payload.initialData;
  return raw && typeof raw === 'object' && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : {};
}

function skillsListDisplayModeFromPayload(payload: BootPayload): CollectionListDisplayMode | null {
  const value = skillsBootData(payload).skillsListDisplayMode;
  return typeof value === 'string' && isCollectionListDisplayMode(value) ? value : null;
}

type MarkdownToken = {
  type: string;
  text?: string;
  tokens?: MarkdownToken[];
  depth?: number;
  ordered?: boolean;
  items?: MarkdownListItem[];
  lang?: string;
  href?: string;
  title?: string;
};

type MarkdownListItem = {
  text?: string;
  tokens?: MarkdownToken[];
};

function markdownTokens(markdown: string): MarkdownToken[] {
  return marked.lexer(markdown) as unknown as MarkdownToken[];
}

function isSafeMarkdownHref(value: string): boolean {
  if (!value) {
    return false;
  }
  if (value.startsWith('#') || value.startsWith('/')) {
    return true;
  }

  try {
    const url = new URL(value, window.location.origin);
    return ['http:', 'https:', 'mailto:'].includes(url.protocol);
  } catch {
    return false;
  }
}

function renderInlineTokens(tokens: MarkdownToken[] | undefined, fallback: string | undefined, keyPrefix: string): ReactNode {
  const effectiveTokens = tokens && tokens.length > 0 ? tokens : fallback ? [{ type: 'text', text: fallback }] : [];
  return effectiveTokens.map((token, index) => {
    const key = `${keyPrefix}-${index}`;
    switch (token.type) {
      case 'strong':
        return <strong key={key}>{renderInlineTokens(token.tokens, token.text, key)}</strong>;
      case 'em':
        return <em key={key}>{renderInlineTokens(token.tokens, token.text, key)}</em>;
      case 'codespan':
        return <code key={key}>{token.text || ''}</code>;
      case 'br':
        return <br key={key} />;
      case 'link': {
        const children = renderInlineTokens(token.tokens, token.text, key);
        if (!token.href || !isSafeMarkdownHref(token.href)) {
          return <Fragment key={key}>{children}</Fragment>;
        }
        return (
          <a key={key} href={token.href} title={token.title} rel="noopener noreferrer nofollow">
            {children}
          </a>
        );
      }
      case 'image':
      case 'html':
        return null;
      default:
        return <Fragment key={key}>{token.text || ''}</Fragment>;
    }
  });
}

function renderBlockToken(token: MarkdownToken, key: string): ReactNode {
  switch (token.type) {
    case 'heading': {
      const depth = Math.min(Math.max(token.depth || 2, 1), 6) as 1 | 2 | 3 | 4 | 5 | 6;
      const HeadingTag = `h${depth}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
      return (
        <HeadingTag key={key} className="mt-5 font-semibold text-slate-950 first:mt-0 dark:text-white">
          {renderInlineTokens(token.tokens, token.text, key)}
        </HeadingTag>
      );
    }
    case 'paragraph':
      return (
        <p key={key} className="mt-3 first:mt-0">
          {renderInlineTokens(token.tokens, token.text, key)}
        </p>
      );
    case 'blockquote':
      return (
        <blockquote key={key} className="mt-4 border-l-4 border-mm-border pl-4 text-slate-600 dark:text-slate-400">
          {renderMarkdownBlocks(token.tokens || [], key)}
        </blockquote>
      );
    case 'list': {
      const ListTag = token.ordered ? 'ol' : 'ul';
      return (
        <ListTag key={key} className="mt-3 list-outside space-y-2 pl-6">
          {(token.items || []).map((item, index) => (
            <li key={`${key}-${index}`} className={token.ordered ? 'list-decimal' : 'list-disc'}>
              {item.tokens && item.tokens.length > 0
                ? renderMarkdownBlocks(item.tokens, `${key}-${index}`)
                : renderInlineTokens(undefined, item.text, `${key}-${index}`)}
            </li>
          ))}
        </ListTag>
      );
    }
    case 'code':
      return (
        <pre key={key} className="mt-4 overflow-x-auto rounded-lg bg-slate-950 p-4 text-slate-100">
          <code className={token.lang ? `language-${token.lang}` : undefined}>{token.text || ''}</code>
        </pre>
      );
    case 'text':
      return <Fragment key={key}>{renderInlineTokens(token.tokens, token.text, key)}</Fragment>;
    case 'hr':
      return <hr key={key} className="my-5 border-mm-border" />;
    case 'space':
    case 'html':
      return null;
    default:
      return token.text || (token.tokens && token.tokens.length > 0) ? (
        <p key={key} className="mt-3 first:mt-0">
          {renderInlineTokens(token.tokens, token.text, key)}
        </p>
      ) : null;
  }
}

function renderMarkdownBlocks(tokens: MarkdownToken[], keyPrefix: string): ReactNode {
  return tokens.map((token, index) => renderBlockToken(token, `${keyPrefix}-${index}`));
}

function MarkdownRenderer({ markdown }: { markdown: string }) {
  const tokens = useMemo(() => markdownTokens(markdown), [markdown]);
  if (tokens.length === 0) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">No markdown content is available for this skill.</p>;
  }
  return <>{renderMarkdownBlocks(tokens, 'skill-markdown')}</>;
}

function recordLastSelectedSkill(skillId: string): void {
  updateDashboardPreferences({ lastSelectedSkillId: skillId });
}

function SkillSidebar({
  skills,
  activeSkillId,
  filterText,
  setFilterText,
  skillsQuery,
}: {
  skills: SkillCatalogItem[];
  activeSkillId: string | null;
  filterText: string;
  setFilterText: (value: string) => void;
  skillsQuery: UseQueryResult<SkillCatalogItem[], Error>;
}) {
  const rows = useMemo(
    (): CollectionSidebarRow[] => skills.map((item) => ({
      id: item.id,
      href: skillDetailHref(item.id),
      primaryText: item.label ?? item.id,
    })),
    [skills],
  );
  const pinnedRow = useMemo((): CollectionSidebarRow | null => {
    const active = activeSkillId ? skills.find((item) => item.id === activeSkillId) : null;
    return active
      ? { id: active.id, href: skillDetailHref(active.id), primaryText: active.label ?? active.id }
      : null;
  }, [activeSkillId, skills]);

  return (
    <CollectionSidebar
      landmarkLabel="Skill navigation"
      tableLabel="Skill list table slice"
      header="Skill"
      filterLabel="Skill filter"
      filterPlaceholder="Filter skills"
      rows={rows}
      activeId={activeSkillId}
      pinnedRow={pinnedRow}
      isLoading={skillsQuery.isLoading}
      error={skillsQuery.isError && skills.length === 0 ? skillsQuery.error : null}
      onRetry={() => void skillsQuery.refetch()}
      loadingCopy="Loading skills..."
      emptyCopy="No skills available yet."
      filteredEmptyCopy="No skills match your filter."
      errorCopy="Failed to load skills."
      currentRowCopy="Current skill"
      filterValue={filterText}
      onFilterChange={setFilterText}
      className="skill-collection-sidebar"
      headerContent={(
        <CollectionSidebarFilterHeader
          copy={SKILL_SIDEBAR_FILTER_COPY}
          filterText={filterText}
          setFilterText={setFilterText}
        />
      )}
      renderLink={(row, props) => (
        <Link
          to={row.href}
          {...props}
          onClick={() => recordLastSelectedSkill(row.id)}
        />
      )}
    />
  );
}

function SkillsCatalogTable({
  skills,
  filterText,
  setFilterText,
  skillsQuery,
}: {
  skills: SkillCatalogItem[];
  filterText: string;
  setFilterText: (value: string) => void;
  skillsQuery: UseQueryResult<SkillCatalogItem[], Error>;
}) {
  const columns: Column<SkillCatalogItem>[] = [
    {
      key: 'skill',
      header: (
        <CollectionColumnFilter
          copy={SKILL_SIDEBAR_FILTER_COPY}
          filterText={filterText}
          setFilterText={setFilterText}
        />
      ),
      width: 'var(--workflow-list-column-workflow-width)',
      render: (item) => (
        <Link
          to={skillDetailHref(item.id)}
          className="skills-catalog-title-link"
          onClick={() => recordLastSelectedSkill(item.id)}
        >
          <span className="skills-catalog-title">{item.label ?? item.id}</span>
          {item.label && item.label !== item.id ? (
            <span className="skills-catalog-id">{item.id}</span>
          ) : null}
        </Link>
      ),
    },
    {
      key: 'description',
      header: 'Description',
      render: (item) => item.description ?? '—',
    },
    {
      key: 'source',
      header: 'Source',
      render: (item) => skillSourceLabel(item.sourceKind),
    },
    {
      key: 'inputs',
      header: 'Inputs',
      render: (item) => (item.hasInputSchema ? 'Structured inputs' : '—'),
    },
    {
      key: 'content',
      header: 'Content',
      render: (item) => skillContentSummary(item),
    },
  ];

  return (
    <DataTable
      data={skills}
      columns={columns}
      getRowKey={(item) => item.id}
      ariaLabel="Skills catalog"
      isLoading={skillsQuery.isLoading}
      loadingMessage="Loading skills..."
      isError={skillsQuery.isError}
      errorMessage={(
        <>
          <span>Failed to load skills.</span>{' '}
          <button type="button" className="secondary" onClick={() => void skillsQuery.refetch()}>
            Retry
          </button>
        </>
      )}
      emptyMessage={filterText.trim() ? 'No skills match your filter.' : 'No skills available yet.'}
      rowActions={(item) => (
        <Link
          to={skillDetailHref(item.id)}
          className="skills-catalog-open-link"
          aria-label={`Open skill ${item.label ?? item.id}`}
          onClick={() => recordLastSelectedSkill(item.id)}
        >
          Open
        </Link>
      )}
      rowActionsHeader="Action"
    />
  );
}

function SkillDetail({
  routeSkillId,
  selectedSkill,
  skillsQuery,
  previewTab,
  setPreviewTab,
  detailHeadingRef,
}: {
  routeSkillId: string | null;
  selectedSkill: SkillCatalogItem | null;
  skillsQuery: UseQueryResult<SkillCatalogItem[], Error>;
  previewTab: PreviewTab;
  setPreviewTab: (tab: PreviewTab) => void;
  detailHeadingRef: React.RefObject<HTMLHeadingElement | null>;
}) {
  return (
    <section className="min-w-0 rounded-2xl border border-mm-border/80 bg-transparent p-4 shadow-sm sm:p-6">
      {selectedSkill ? (
        <div className="space-y-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
              Skill Details
            </p>
            <h3 ref={detailHeadingRef} tabIndex={-1} className="mt-2 text-2xl font-semibold text-slate-950 dark:text-white">
              {selectedSkill.label ?? selectedSkill.id}
            </h3>
            {selectedSkill.description ? (
              <p className="mt-2 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
                {selectedSkill.description}
              </p>
            ) : null}
          </div>

          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Skill preview tabs">
            {(
              [
                ['rendered', 'Rendered'],
                ['raw', 'Raw Markdown'],
                ['metadata', 'Metadata'],
              ] as Array<[PreviewTab, string]>
            ).map(([tab, label]) => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={previewTab === tab}
                tabIndex={previewTab === tab ? 0 : -1}
                className={previewTab === tab ? 'queue-submit-primary' : 'secondary'}
                onClick={() => setPreviewTab(tab)}
              >
                {label}
              </button>
            ))}
          </div>

          {previewTab === 'rendered' ? (
            <div
              className="min-w-0 break-words text-sm leading-7 text-slate-700 dark:text-slate-300 [&_a]:break-words [&_a]:text-mm-accent [&_a]:underline [&_:not(pre)_>_code]:break-words [&_:not(pre)_>_code]:rounded [&_:not(pre)_>_code]:bg-slate-100 [&_:not(pre)_>_code]:px-1.5 [&_:not(pre)_>_code]:py-0.5 [&_:not(pre)_>_code]:font-mono [&_:not(pre)_>_code]:text-xs [&_:not(pre)_>_code]:text-slate-900 dark:[&_:not(pre)_>_code]:bg-slate-900 dark:[&_:not(pre)_>_code]:text-slate-100"
              data-testid="skill-markdown-preview"
            >
              <MarkdownRenderer markdown={selectedSkill.markdown || ''} />
            </div>
          ) : previewTab === 'raw' ? (
            <pre
              className="min-w-0 overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950 p-4 text-xs leading-6 text-slate-100"
              data-testid="skill-raw-markdown"
              tabIndex={0}
              aria-label="Raw Markdown Content"
            >
              {selectedSkill.markdown || ''}
            </pre>
          ) : (
            <dl
              className="grid gap-2 text-sm text-slate-700 dark:text-slate-300"
              data-testid="skill-metadata"
            >
              <div className="flex justify-between gap-4">
                <dt className="font-semibold text-slate-900 dark:text-white">ID</dt>
                <dd className="break-all text-right">{selectedSkill.id}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="font-semibold text-slate-900 dark:text-white">Source</dt>
                <dd>{skillSourceLabel(selectedSkill.sourceKind)}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="font-semibold text-slate-900 dark:text-white">Structured inputs</dt>
                <dd>{selectedSkill.hasInputSchema ? 'Yes' : 'No'}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="font-semibold text-slate-900 dark:text-white">Characters</dt>
                <dd>{(selectedSkill.markdown || '').length}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="font-semibold text-slate-900 dark:text-white">Lines</dt>
                <dd>{selectedSkill.markdown ? selectedSkill.markdown.split('\n').length : 0}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="font-semibold text-slate-900 dark:text-white">Has content</dt>
                <dd>{selectedSkill.markdown && selectedSkill.markdown.trim() ? 'Yes' : 'No'}</dd>
              </div>
            </dl>
          )}
        </div>
      ) : skillsQuery.isLoading ? (
        <LoadingPlaceholder
          surface="skills"
          region="preview"
          variant="detail"
          density="detail-heavy"
          preserveContext
        />
      ) : skillsQuery.isError ? (
        <p className="text-sm text-mm-danger">Failed to load skills.</p>
      ) : routeSkillId ? (
        <div role="status" data-testid="skill-not-found">
          <p className="text-sm font-semibold text-slate-900 dark:text-white">
            Skill "{routeSkillId}" was not found.
          </p>
          <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
            It may have been removed or renamed. Choose another skill, or return to the{' '}
            <Link to="/skills" className="text-mm-accent underline">skills catalog</Link>.
          </p>
        </div>
      ) : (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Select a skill to preview its markdown content.
        </p>
      )}
    </section>
  );
}

export function SkillsPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const [filterText, setFilterText] = useState('');
  const [previewTab, setPreviewTab] = useState<PreviewTab>('rendered');
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [name, setName] = useState('');
  const [markdown, setMarkdown] = useState('');
  const [showCreatePreview, setShowCreatePreview] = useState(false);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [collisionPolicy, setCollisionPolicy] = useState<CollisionPolicy>('reject');
  const [message, setMessage] = useState<string | null>(null);

  const drawerRef = useRef<HTMLDivElement | null>(null);
  const drawerTriggerRef = useRef<HTMLButtonElement | null>(null);
  const detailHeadingRef = useRef<HTMLHeadingElement | null>(null);

  // Selection is route-derived: `/skills/:skillId` selects a skill, `/skills`
  // is the full catalog table.
  const routeSkillId = useMemo(
    () => decodeSkillDetail(location.pathname)?.skillId ?? null,
    [location.pathname],
  );

  // The dashboard shell injects the resolved Skills display mode. On a detail
  // route, a `table` value is coerced to `sidebar` so a direct visit never
  // redirects away from the requested skill; `/skills` is always the table.
  const payloadDisplayMode = skillsListDisplayModeFromPayload(payload);
  const listDisplayMode: CollectionListDisplayMode = routeSkillId
    ? (payloadDisplayMode === 'hidden' ? 'hidden' : 'sidebar')
    : 'table';

  const skillsQuery = useQuery({
    queryKey: ['skills', 'detail'],
    queryFn: async (): Promise<SkillCatalogItem[]> => {
      const response = await fetch('/api/workflows/skills?includeContent=true', {
        headers: {
          Accept: 'application/json',
        },
      });
      if (!response.ok) {
        throw new Error('Failed to load skills.');
      }
      return normalizeSkillCatalog(await response.json());
    },
  });

  const skills = useMemo(() => skillsQuery.data || [], [skillsQuery.data]);
  const filteredSkills = useMemo(
    () => skills.filter((item) => skillMatchesFilter(item, filterText)),
    [filterText, skills],
  );
  const selectedSkill = useMemo(
    () => (routeSkillId ? skills.find((item) => item.id === routeSkillId) ?? null : null),
    [routeSkillId, skills],
  );

  // Reset the preview tab and move focus to the detail heading when the
  // selected skill changes via navigation (not on the initial route load).
  const previousRouteSkillIdRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    const previous = previousRouteSkillIdRef.current;
    previousRouteSkillIdRef.current = routeSkillId;
    if (previous === undefined || previous === routeSkillId) {
      return;
    }
    setPreviewTab('rendered');
    setMessage(null);
    if (routeSkillId) {
      window.requestAnimationFrame(() => detailHeadingRef.current?.focus({ preventScroll: true }));
    }
  }, [routeSkillId]);

  const closeDrawer = useCallback(() => {
    setIsDrawerOpen(false);
    setMessage(null);
    drawerTriggerRef.current?.focus();
  }, []);

  const openDrawer = useCallback(() => {
    setMessage(null);
    setShowCreatePreview(false);
    setIsDrawerOpen(true);
  }, []);

  // Focus the first field when the drawer opens so keyboard users land inside
  // the dialog rather than the inert background.
  useEffect(() => {
    if (!isDrawerOpen) {
      return;
    }
    const root = drawerRef.current;
    if (!root) {
      return;
    }
    const firstField = root.querySelector<HTMLElement>('input, textarea, select, button');
    firstField?.focus();
  }, [isDrawerOpen]);

  const handleDrawerKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      closeDrawer();
      return;
    }
    if (event.key !== 'Tab') {
      return;
    }
    const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter((element) => {
      const isVisible = element.offsetWidth > 0 || element.offsetHeight > 0;
      const isNotAriaHidden = element.getAttribute('aria-hidden') !== 'true';
      const isNotTabIndexMinusOne = element.getAttribute('tabindex') !== '-1';
      return isVisible && isNotAriaHidden && isNotTabIndexMinusOne;
    });
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first || !last) {
      event.preventDefault();
      return;
    }
    if (!drawerRef.current?.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
      return;
    }
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, [closeDrawer]);

  const openCreatedSkill = useCallback((skillId: string) => {
    if (!skillId) {
      return;
    }
    recordLastSelectedSkill(skillId);
    navigate(skillDetailHref(skillId));
  }, [navigate]);

  const createMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch('/api/workflows/skills', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          name,
          markdown,
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || 'Failed to create skill.');
      }
      return response.json();
    },
    onSuccess: async () => {
      const createdSkillId = name.trim();
      await queryClient.invalidateQueries({ queryKey: ['skills', 'detail'] });
      setName('');
      setMarkdown('');
      setShowCreatePreview(false);
      setIsDrawerOpen(false);
      setMessage(null);
      openCreatedSkill(createdSkillId);
    },
    onError: (error) => {
      setMessage(error instanceof Error ? error.message : 'Failed to create skill.');
    },
  });

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!zipFile) {
        throw new Error('Choose a skill zip file to upload.');
      }
      const body = new FormData();
      body.append('file', zipFile);
      body.append('collision_policy', collisionPolicy);
      const response = await fetch('/api/skills/imports', {
        method: 'POST',
        headers: {
          Accept: 'application/json',
        },
        body,
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || 'Failed to upload skill zip.');
      }
      return response.json() as Promise<{ name?: string; skill?: string }>;
    },
    onSuccess: async (result) => {
      const uploadedSkillId = result.name || result.skill || zipFile?.name.replace(/\.zip$/i, '') || '';
      await queryClient.invalidateQueries({ queryKey: ['skills', 'detail'] });
      setIsDrawerOpen(false);
      setZipFile(null);
      setMessage(null);
      openCreatedSkill(uploadedSkillId);
    },
    onError: (error) => {
      setMessage(error instanceof Error ? error.message : 'Failed to upload skill zip.');
    },
  });

  const handleCreateSubmit = () => {
    const nameError = validateSkillName(name);
    if (nameError) {
      setMessage(nameError);
      return;
    }
    if (!markdown.trim()) {
      setMessage('Skill markdown is required.');
      return;
    }
    createMutation.mutate();
  };

  const pageHeader = (
    <header className="flex items-center justify-end gap-4 px-1 sm:px-0">
      <button
        ref={drawerTriggerRef}
        type="button"
        className="skills-create-button shrink-0"
        onClick={openDrawer}
        aria-label="Create New Skill"
        title="Create New Skill"
      >
        <Plus size={20} aria-hidden="true" />
      </button>
    </header>
  );

  const createDrawer = isDrawerOpen ? (
    <div
      className="fixed inset-0 z-[120] flex justify-end bg-[rgb(var(--mm-ink)/0.45)]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          closeDrawer();
        }
      }}
    >
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="Create or upload skill"
        className="flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-mm-border/80 bg-[rgb(var(--mm-panel))] p-5 shadow-2xl"
        onKeyDown={handleDrawerKeyDown}
      >
        <header className="flex items-center justify-between gap-3">
          <h3 className="text-xl font-semibold text-slate-900 dark:text-white">Create Skill</h3>
          <button
            type="button"
            className="secondary"
            onClick={closeDrawer}
            aria-label="Close create skill"
          >
            <span aria-hidden="true">×</span>
          </button>
        </header>

        <form
          className="mt-4"
          onSubmit={(event) => {
            event.preventDefault();
            handleCreateSubmit();
          }}
        >
          <label>
            Skill Name
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              disabled={createMutation.isPending}
            />
          </label>
          <label>
            Skill Markdown
            <textarea
              value={markdown}
              onChange={(event) => setMarkdown(event.target.value)}
              disabled={createMutation.isPending}
            />
          </label>
          <div className="actions">
            <button type="submit" className="queue-submit-primary" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Saving...' : 'Save Skill'}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => setShowCreatePreview((value) => !value)}
            >
              {showCreatePreview ? 'Hide Preview' : 'Show Preview'}
            </button>
            <button type="button" className="secondary" onClick={closeDrawer}>
              Cancel
            </button>
          </div>
          {showCreatePreview ? (
            <div className="mt-4 rounded-lg border border-mm-border/80 p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500 dark:text-slate-400">
                Preview
              </p>
              <div
                className="mt-2 min-w-0 break-words text-sm leading-7 text-slate-700 dark:text-slate-300 [&_a]:text-mm-accent [&_a]:underline"
                data-testid="skill-create-preview"
              >
                <MarkdownRenderer markdown={markdown} />
              </div>
            </div>
          ) : null}
        </form>

        <div className="mt-6 border-t border-mm-border/80 pt-5">
          <h4 className="text-base font-semibold text-slate-900 dark:text-white">Upload Skill Zip</h4>
          <label>
            Skill Zip
            <input
              type="file"
              accept=".zip,application/zip"
              disabled={uploadMutation.isPending}
              onChange={(event) => {
                setZipFile(event.target.files?.[0] || null);
                setMessage(null);
              }}
            />
          </label>
          <label>
            Collision Policy
            <select
              value={collisionPolicy}
              disabled={uploadMutation.isPending}
              onChange={(event) => setCollisionPolicy(event.target.value as CollisionPolicy)}
            >
              {COLLISION_POLICIES.map((policy) => (
                <option key={policy.value} value={policy.value}>
                  {policy.label}
                </option>
              ))}
            </select>
          </label>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400" data-testid="collision-policy-help">
            {COLLISION_POLICIES.find((policy) => policy.value === collisionPolicy)?.description}
          </p>
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={uploadMutation.isPending}
              onClick={() => uploadMutation.mutate()}
            >
              {uploadMutation.isPending ? 'Uploading...' : 'Upload Zip'}
            </button>
          </div>
        </div>

        <p className={`queue-submit-message${message ? ' notice error' : ''}`}>{message || ''}</p>
      </div>
    </div>
  ) : null;

  if (listDisplayMode === 'table') {
    return (
      <CollectionWorkspace
        collection="skill"
        mode={isDrawerOpen ? 'create' : 'table'}
        className="skills-page skills-catalog-page"
        primaryAs="div"
        primaryClassName="skills-catalog-primary px-4 py-4 sm:px-6 sm:py-6"
        primaryLabel="Skills catalog"
        data-skills-list-display-mode="table"
      >
        <div className="space-y-5 sm:space-y-6">
          {pageHeader}
          <SkillsCatalogTable
            skills={filteredSkills}
            filterText={filterText}
            setFilterText={setFilterText}
            skillsQuery={skillsQuery}
          />
        </div>
        {createDrawer}
      </CollectionWorkspace>
    );
  }

  return (
    <CollectionWorkspace
      collection="skill"
      mode={isDrawerOpen ? 'create' : listDisplayMode === 'sidebar' ? 'sidebar' : 'detail'}
      className="workflow-workspace-shell collection-workspace--edge-rail skills-page"
      primaryAs="div"
      primaryClassName="workflow-workspace-detail skills-detail-primary"
      primaryLabel="Skill detail"
      data-sidebar-collapsed={listDisplayMode === 'sidebar' ? 'false' : 'true'}
      data-skills-list-display-mode={listDisplayMode}
      sidebar={listDisplayMode === 'sidebar' ? (
        <SkillSidebar
          skills={skills}
          activeSkillId={routeSkillId}
          filterText={filterText}
          setFilterText={setFilterText}
          skillsQuery={skillsQuery}
        />
      ) : null}
    >
      <div className="space-y-5 sm:space-y-6">
        {pageHeader}
        <SkillDetail
          routeSkillId={routeSkillId}
          selectedSkill={selectedSkill}
          skillsQuery={skillsQuery}
          previewTab={previewTab}
          setPreviewTab={setPreviewTab}
          detailHeadingRef={detailHeadingRef}
        />
      </div>
      {createDrawer}
    </CollectionWorkspace>
  );
}
export default SkillsPage;
