import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { marked } from 'marked';

import type { BootPayload } from '../boot/parseBootPayload';

interface SkillItem {
  id: string;
  markdown: string | null;
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
  const [isCreating, setIsCreating] = useState(false);
  const [name, setName] = useState('');
  const [markdown, setMarkdown] = useState('');
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const skillsQuery = useQuery({
    queryKey: ['skills', 'detail'],
    queryFn: async (): Promise<SkillItem[]> => {
      const response = await fetch('/api/tasks/skills?includeContent=true', {
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

  useEffect(() => {
    if (!selectedSkillId && (skillsQuery.data?.length || 0) > 0) {
      setSelectedSkillId(skillsQuery.data?.[0]?.id || '');
    }
  }, [selectedSkillId, skillsQuery.data]);

  const createMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch('/api/tasks/skills', {
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
      setIsCreating(false);
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
      const response = await fetch('/api/tasks/skills/upload', {
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
      return response.json() as Promise<{ skill?: string }>;
    },
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['skills', 'detail'] });
      setSelectedSkillId(result.skill || zipFile?.name.replace(/\.zip$/i, '') || '');
      setIsCreating(false);
      setZipFile(null);
      setMessage(null);
    },
    onError: (error) => {
      setMessage(error instanceof Error ? error.message : 'Failed to upload skill zip.');
    },
  });

  const selectedSkill = useMemo(
    () => (skillsQuery.data || []).find((item) => item.id === selectedSkillId) || null,
    [selectedSkillId, skillsQuery.data],
  );

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="space-y-6">
        <header className="rounded-[2rem] border border-mm-border/80 bg-transparent px-6 py-6 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
            Agent Skills
          </p>
          <h2 className="mt-2 text-3xl font-semibold tracking-tight text-slate-950 dark:text-white">
            Skills
          </h2>
          <p className="mt-2 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Inspect runtime-visible skills and create local additions without the legacy dashboard renderer.
          </p>
        </header>

        <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <section className="rounded-[2rem] border border-mm-border/80 bg-transparent p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-base font-semibold text-slate-900 dark:text-white">Available Skills</h3>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setIsCreating(true);
                  setMessage(null);
                }}
              >
                Create New Skill
              </button>
            </div>

            <div className="mt-4 grid gap-2">
              {(skillsQuery.data || []).map((skillItem) => (
                <button
                  key={skillItem.id}
                  type="button"
                  className={selectedSkillId === skillItem.id ? 'queue-submit-primary' : 'secondary'}
                  onClick={() => {
                    setSelectedSkillId(skillItem.id);
                    setIsCreating(false);
                    setMessage(null);
                  }}
                >
                  {skillItem.id}
                </button>
              ))}
            </div>
          </section>

          <section className="rounded-[2rem] border border-mm-border/80 bg-transparent p-6 shadow-sm">
            {isCreating ? (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!name.trim() || !markdown.trim()) {
                    setMessage('Skill name and markdown are required.');
                    return;
                  }
                  createMutation.mutate();
                }}
              >
                <h3 className="text-xl font-semibold text-slate-900 dark:text-white">Create Skill</h3>
                <label>
                  Skill Name
                  <input value={name} onChange={(event) => setName(event.target.value)} />
                </label>
                <label>
                  Skill Markdown
                  <textarea value={markdown} onChange={(event) => setMarkdown(event.target.value)} />
                </label>
                <div className="actions">
                  <button
                    type="submit"
                    className="queue-submit-primary"
                    disabled={createMutation.isPending}
                  >
                    {createMutation.isPending ? 'Saving...' : 'Save Skill'}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => {
                      setIsCreating(false);
                      setMessage(null);
                    }}
                  >
                    Cancel
                  </button>
                </div>
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
                <p className={`queue-submit-message${message ? ' notice error' : ''}`}>
                  {message || ''}
                </p>
              </form>
            ) : selectedSkill ? (
              <div className="space-y-4">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
                    Skill Preview
                  </p>
                  <h3 className="mt-2 text-2xl font-semibold text-slate-950 dark:text-white">
                    {selectedSkill.id}
                  </h3>
                </div>
                <div
                  className="text-sm leading-7 text-slate-700 dark:text-slate-300 [&_a]:text-mm-accent [&_a]:underline [&_code]:rounded [&_code]:bg-slate-100 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs [&_code]:text-slate-900 dark:[&_code]:bg-slate-900 dark:[&_code]:text-slate-100"
                  data-testid="skill-markdown-preview"
                >
                  <MarkdownRenderer markdown={selectedSkill.markdown || ''} />
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Select a skill to preview its markdown content.
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
export default SkillsPage;
