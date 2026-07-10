import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { marked } from 'marked';

import type { BootPayload } from '../boot/parseBootPayload';
import { LoadingPlaceholder } from '../components/dashboard/LoadingPlaceholder';

interface SkillItem {
  id: string;
  markdown: string | null;
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

interface SkillsResponse {
  items?: {
    worker?: string[];
  };
  legacyItems?: SkillItem[];
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

export function SkillsPage({ payload: _payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const [selectedSkillId, setSelectedSkillId] = useState<string>('');
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

  const skillsQuery = useQuery({
    queryKey: ['skills', 'detail'],
    queryFn: async (): Promise<SkillItem[]> => {
      const response = await fetch('/api/workflows/skills?includeContent=true', {
        headers: {
          Accept: 'application/json',
        },
      });
      if (!response.ok) {
        throw new Error('Failed to load skills.');
      }
      const payload = (await response.json()) as SkillsResponse;
      return payload.legacyItems || [];
    },
  });

  const skills = skillsQuery.data || [];

  const filteredSkills = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    if (!needle) {
      return skills;
    }
    return skills.filter((item) => item.id.toLowerCase().includes(needle));
  }, [filterText, skills]);

  useEffect(() => {
    if (!selectedSkillId && skills.length > 0) {
      setSelectedSkillId(skills[0]?.id || '');
    }
  }, [selectedSkillId, skills]);

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

  const selectSkill = useCallback((skillId: string) => {
    setSelectedSkillId(skillId);
    setPreviewTab('rendered');
    setMessage(null);
    window.requestAnimationFrame(() => detailHeadingRef.current?.focus({ preventScroll: true }));
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
      await queryClient.invalidateQueries({ queryKey: ['skills', 'detail'] });
      setSelectedSkillId(name.trim());
      setPreviewTab('rendered');
      setName('');
      setMarkdown('');
      setShowCreatePreview(false);
      setIsDrawerOpen(false);
      setMessage(null);
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
      await queryClient.invalidateQueries({ queryKey: ['skills', 'detail'] });
      setSelectedSkillId(result.name || result.skill || zipFile?.name.replace(/\.zip$/i, '') || '');
      setPreviewTab('rendered');
      setIsDrawerOpen(false);
      setZipFile(null);
      setMessage(null);
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

  const selectedSkill = useMemo(
    () => skills.find((item) => item.id === selectedSkillId) || null,
    [selectedSkillId, skills],
  );

  return (
    <div className="skills-page mx-auto w-full max-w-7xl px-2 py-4 sm:px-6 sm:py-6 lg:px-8">
      <div className="space-y-5 sm:space-y-6">
        <header className="px-1 sm:px-0">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
            Agent Skills
          </p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl dark:text-white">
            Skills
          </h2>
          <p className="mt-2 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Inspect runtime-visible skills and create local additions without the legacy dashboard renderer.
          </p>
        </header>

        <div className="grid gap-5 sm:gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <section className="min-w-0 rounded-2xl border border-mm-border/80 bg-transparent p-4 shadow-sm" aria-label="Skill navigation">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-base font-semibold text-slate-900 dark:text-white">Available Skills</h3>
              <button ref={drawerTriggerRef} type="button" className="secondary" onClick={openDrawer}>
                Create New Skill
              </button>
            </div>

            <label className="mt-4 block text-xs font-semibold uppercase tracking-[0.18em] text-slate-500 dark:text-slate-400">
              Filter skills
              <input
                type="search"
                className="mt-1 w-full font-normal normal-case tracking-normal"
                placeholder="Filter skills by ID"
                value={filterText}
                onChange={(event) => setFilterText(event.target.value)}
                aria-label="Filter skills by ID"
              />
            </label>

            <div className="mt-4 grid gap-2" data-testid="skill-list" aria-label="Available skills" aria-busy={skillsQuery.isLoading}>
              {skillsQuery.isLoading ? (
                <LoadingPlaceholder
                  surface="skills"
                  region="catalog"
                  variant="catalog"
                  density="compact"
                  preserveContext
                />
              ) : skillsQuery.isError ? (
                <div className="space-y-2" role="alert">
                  <p className="text-sm text-mm-danger">Failed to load skills.</p>
                  <button type="button" className="secondary" onClick={() => skillsQuery.refetch()}>
                    Retry
                  </button>
                </div>
              ) : skills.length === 0 ? (
                <p className="text-sm text-slate-500 dark:text-slate-400" role="status">
                  No skills available yet. Create one to get started.
                </p>
              ) : filteredSkills.length === 0 ? (
                <p className="text-sm text-slate-500 dark:text-slate-400" role="status">No skills match your filter.</p>
              ) : (
                filteredSkills.map((skillItem) => (
                  <button
                    key={skillItem.id}
                    type="button"
                    aria-current={selectedSkillId === skillItem.id ? 'true' : 'false'}
                    className={selectedSkillId === skillItem.id ? 'queue-submit-primary' : 'secondary'}
                    onClick={() => selectSkill(skillItem.id)}
                  >
                    {skillItem.id}
                  </button>
                ))
              )}
            </div>
          </section>

          <section className="min-w-0 rounded-2xl border border-mm-border/80 bg-transparent p-4 shadow-sm sm:p-6">
            {selectedSkill ? (
              <div className="space-y-4">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
                    Skill Preview
                  </p>
                  <h3 ref={detailHeadingRef} tabIndex={-1} className="mt-2 text-2xl font-semibold text-slate-950 dark:text-white">
                    {selectedSkill.id}
                  </h3>
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
            ) : (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Select a skill to preview its markdown content.
              </p>
            )}
          </section>
        </div>
      </div>

      {isDrawerOpen ? (
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
                <input value={name} onChange={(event) => setName(event.target.value)} />
              </label>
              <label>
                Skill Markdown
                <textarea value={markdown} onChange={(event) => setMarkdown(event.target.value)} />
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
      ) : null}
    </div>
  );
}
export default SkillsPage;
