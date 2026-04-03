import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { mountPage } from '../boot/mountPage';
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

const ALLOWED_MARKDOWN_TAGS = new Set([
  'a',
  'blockquote',
  'br',
  'code',
  'em',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'li',
  'ol',
  'p',
  'pre',
  'strong',
  'ul',
]);

function isSafeHref(value: string): boolean {
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

function sanitizeHtml(html: string): string {
  const template = document.createElement('template');
  template.innerHTML = html;

  for (const element of Array.from(template.content.querySelectorAll('*'))) {
    const tagName = element.tagName.toLowerCase();
    if (!ALLOWED_MARKDOWN_TAGS.has(tagName)) {
      element.replaceWith(document.createTextNode(element.textContent || ''));
      continue;
    }

    for (const attribute of Array.from(element.attributes)) {
      const attributeName = attribute.name.toLowerCase();
      if (attributeName.startsWith('on')) {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (tagName === 'a' && (attributeName === 'href' || attributeName === 'title')) {
        continue;
      }
      element.removeAttribute(attribute.name);
    }

    if (tagName === 'a') {
      const href = element.getAttribute('href');
      if (!href || !isSafeHref(href)) {
        element.removeAttribute('href');
      } else {
        element.setAttribute('rel', 'noopener noreferrer nofollow');
      }
    }
  }

  return template.innerHTML;
}

function renderMarkdown(markdown: string): string {
  if (window.marked && typeof window.marked.parse === 'function') {
    return sanitizeHtml(window.marked.parse(markdown));
  }
  return sanitizeHtml(
    `<pre>${markdown.replace(/[&<>]/g, (value) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[value] || value))}</pre>`,
  );
}

export function SkillsPage({ payload: _payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const [selectedSkillId, setSelectedSkillId] = useState<string>('');
  const [isCreating, setIsCreating] = useState(false);
  const [name, setName] = useState('');
  const [markdown, setMarkdown] = useState('');
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
                  className="space-y-3 text-sm leading-7 text-slate-700 dark:text-slate-300"
                  dangerouslySetInnerHTML={{
                    __html: renderMarkdown(selectedSkill.markdown || ''),
                  }}
                />
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

mountPage(SkillsPage);
