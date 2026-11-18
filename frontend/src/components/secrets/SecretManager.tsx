import { Button } from '../Button';
import { FormEvent, useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { QueryClient, useMutation } from '@tanstack/react-query';

import { useSettingsDraftGuard, useSettingsDraftRegistration } from '../settings/SettingsDraftGuard';
import { trapTabWithin } from '../../lib/focusTrap';
import { formatStatusLabel } from '../../utils/formatters';
import type { WorkflowActionMenuItem } from '../../lib/workflowActions';
import { WorkflowActionsMenu } from '../WorkflowActionsMenu';

const SECRET_DRAFT_ID = 'managed-secret-editor';

interface SecretMetadata {
  slug: string;
  secretRef?: string;
  status: string;
  details: Record<string, unknown>;
  createdAt: string;
  updatedAt?: string;
  usages?: SecretUsage[];
}

interface SecretUsage {
  consumerType: string;
  objectName: string;
  reference: string;
  scope?: string;
  settingKey?: string;
}

interface SecretUsageResponse {
  secretRef: string;
  usages: SecretUsage[];
  diagnostics?: Array<{
    code: string;
    message: string;
    severity: string;
  }>;
}

interface SecretManagerProps {
  secrets: SecretMetadata[];
  onNotice: (notice: { level: 'ok' | 'error'; text: string } | null) => void;
  queryClient: QueryClient;
}

const _BROKEN_REFERENCE_STATUSES = new Set([
  'disabled',
  'rotated',
  'deleted',
  'invalid',
  'missing',
]);

export function isBrokenReferenceStatus(status: string | undefined): boolean {
  return Boolean(status) && _BROKEN_REFERENCE_STATUSES.has(String(status));
}

/**
 * Focused modal for adding, editing, or rotating one secret. Escape and Cancel
 * go through `onClose`, which protects an unsaved value; focus returns to the
 * control that opened the dialog.
 */
const MORE_ACTIONS_ICON = (
  <svg aria-hidden="true" className="td-workflow-actions-trigger-icon" viewBox="0 0 16 16" focusable="false">
    <circle cx="3" cy="8" r="1.5" />
    <circle cx="8" cy="8" r="1.5" />
    <circle cx="13" cy="8" r="1.5" />
  </svg>
);

const COPY_ICON = (
  <svg aria-hidden="true" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" focusable="false">
    <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
    <path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" />
  </svg>
);

/** Month and day, with the year only when it is not the current one. */
function formatShortDate(date: Date): string {
  if (Number.isNaN(date.getTime())) return '';
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  });
}

function SecretDialog({
  titleId,
  title,
  error,
  onClose,
  children,
}: {
  titleId: string;
  title: string;
  error: string | null;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const firstField = dialogRef.current?.querySelector<HTMLElement>('input:not(:disabled)');
    (firstField ?? dialogRef.current)?.focus();
    return () => opener?.focus();
  }, []);
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }
    trapTabWithin(dialogRef.current, event);
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-4" role="presentation">
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="managed-secret-dialog max-h-[calc(100dvh-2rem)] w-full max-w-lg overflow-y-auto rounded-3xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-700 dark:bg-slate-900"
        onKeyDown={onKeyDown}
      >
        <h4 id={titleId} className="break-words text-lg font-semibold text-slate-900 dark:text-white">
          {title}
        </h4>
        {error ? (
          <p role="alert" className="mt-3 text-sm text-rose-700 dark:text-rose-400">
            {error}
          </p>
        ) : null}
        {children}
      </section>
    </div>
  );
}

// Slugs are free-form, so `/`, `?`, `#` and spaces must not reshape the URL.
function secretPath(slug: string): string {
  return `/api/v1/secrets/${encodeURIComponent(slug)}`;
}

export function SecretManager({
  secrets,
  onNotice,
  queryClient,
}: SecretManagerProps) {
  const [slug, setSlug] = useState('');
  const [plaintext, setPlaintext] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [rotatePromptOpen, setRotatePromptOpen] = useState(false);
  const [rotatePromptSlug, setRotatePromptSlug] = useState('');
  const [rotatePromptVal, setRotatePromptVal] = useState('');
  const automaticUsageAttempts = useRef(new Set<string>());
  const [usageBySlug, setUsageBySlug] = useState<Record<string, SecretUsageResponse>>({});
  const [usageLoadingSlug, setUsageLoadingSlug] = useState<string | null>(null);
  const [usageErrorBySlug, setUsageErrorBySlug] = useState<Record<string, string>>({});

  const { requestDeparture } = useSettingsDraftGuard();
  const discardSecretDraft = () => {
    setSlug('');
    setPlaintext('');
    setIsEditing(false);
    setIsAdding(false);
    setFormError(null);
    setRotatePromptOpen(false);
    setRotatePromptSlug('');
    setRotatePromptVal('');
  };
  // Only entered text is a draft: an Edit or Rotate opened without a new value
  // has nothing to lose.
  useSettingsDraftRegistration(
    SECRET_DRAFT_ID,
    Boolean(plaintext || rotatePromptVal || (!isEditing && slug)),
    discardSecretDraft,
  );
  const editSecret = (targetSlug: string) => {
    requestDeparture(
      () => {
        setSlug(targetSlug);
        setPlaintext('');
        setFormError(null);
        setIsEditing(true);
      },
      `Edit ${targetSlug}? Your unsaved secret draft will be discarded.`,
      [SECRET_DRAFT_ID],
    );
  };

  const addSecret = () => {
    setSlug('');
    setPlaintext('');
    setFormError(null);
    setIsAdding(true);
  };
  const closeSecretDialog = () => {
    requestDeparture(
      discardSecretDraft,
      'Close this secret form? The value you entered will be discarded.',
      [SECRET_DRAFT_ID],
    );
  };

  const refreshSecretQueries = async () => {
    void queryClient.invalidateQueries({ queryKey: ['secrets'] });
    // Even the first inventory read can contain pre-mutation secret readiness.
    const profileInventory = { queryKey: ['provider-profiles'], exact: true };
    await queryClient.cancelQueries(profileInventory);
    void queryClient.invalidateQueries(profileInventory);
  };

  const createOp = useMutation({
    mutationFn: async ({
      slug: nextSlug,
      plaintext: nextPlaintext,
    }: {
      slug: string;
      plaintext: string;
    }) => {
      const response = await fetch('/api/v1/secrets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug: nextSlug, plaintext: nextPlaintext, details: {} }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to create secret');
      }
      return response.json();
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret saved successfully.' });
      discardSecretDraft();
      void refreshSecretQueries();
    },
    onError: (error: Error) => setFormError(error.message),
  });

  const updateOp = useMutation({
    mutationFn: async ({
      slug: nextSlug,
      plaintext: nextPlaintext,
    }: {
      slug: string;
      plaintext: string;
    }) => {
      const response = await fetch(secretPath(nextSlug), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plaintext: nextPlaintext }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to update secret');
      }
      return response.json();
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret updated successfully.' });
      discardSecretDraft();
      void refreshSecretQueries();
    },
    onError: (error: Error) => setFormError(error.message),
  });

  const rotateOp = useMutation({
    mutationFn: async ({
      slug: nextSlug,
      plaintext: nextPlaintext,
    }: {
      slug: string;
      plaintext: string;
    }) => {
      const response = await fetch(`${secretPath(nextSlug)}/rotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plaintext: nextPlaintext }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to rotate secret');
      }
      return response.json();
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret rotated successfully.' });
      discardSecretDraft();
      void refreshSecretQueries();
    },
    onError: (error: Error) => setFormError(error.message),
  });

  const deleteOp = useMutation({
    mutationFn: async (targetSlug: string) => {
      const response = await fetch(secretPath(targetSlug), { method: 'DELETE' });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to delete secret');
      }
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret deleted successfully.' });
      void refreshSecretQueries();
    },
    onError: (error: Error) => onNotice({ level: 'error', text: error.message }),
  });

  const setStatusOp = useMutation({
    mutationFn: async ({
      slug: nextSlug,
      status,
    }: {
      slug: string;
      status: 'active' | 'disabled';
    }) => {
      const response = await fetch(`${secretPath(nextSlug)}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to change secret status');
      }
      return response.json();
    },
    onSuccess: (_data, variables) => {
      const verb = variables.status === 'active' ? 're-enabled' : 'disabled';
      onNotice({ level: 'ok', text: `Secret ${verb}.` });
      void refreshSecretQueries();
    },
    onError: (error: Error) => onNotice({ level: 'error', text: error.message }),
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!slug) {
      setFormError('Secret slug is required.');
      return;
    }
    if (!plaintext) {
      setFormError(
        isEditing
          ? 'Provide a new secure value to update the secret.'
          : 'Provide a secure value for the new secret.',
      );
      return;
    }
    if (isEditing) {
      updateOp.mutate({ slug, plaintext });
      return;
    }
    createOp.mutate({ slug, plaintext });
  };

  const renderStatus = (status: string) => {
    const isBroken = isBrokenReferenceStatus(status);
    const primaryBadge =
      status === 'active' ? (
        <span className="badge badge-success">Active</span>
      ) : status === 'disabled' ? (
        <span className="badge badge-warning">Disabled</span>
      ) : status === 'rotated' ? (
        <span className="badge badge-neutral">Rotated</span>
      ) : (
        <span className="badge badge-error">{formatStatusLabel(status)}</span>
      );
    return (
      <div className="flex flex-wrap items-center gap-1">
        {primaryBadge}
        {isBroken ? (
          <span
            className="badge badge-error"
            aria-label={`Broken reference: ${status}`}
            title="Dependent settings and profiles will fail launches until this secret is active again."
          >
            Broken reference
          </span>
        ) : null}
      </div>
    );
  };

  const copySecretRef = async (secretRef: string) => {
    try {
      await navigator.clipboard.writeText(secretRef);
      onNotice({ level: 'ok', text: 'SecretRef copied.' });
    } catch {
      onNotice({ level: 'error', text: 'Failed to copy SecretRef.' });
    }
  };

  const loadUsage = useCallback(async (targetSlug: string) => {
    setUsageLoadingSlug(targetSlug);
    setUsageErrorBySlug((current) => {
      const next = { ...current };
      delete next[targetSlug];
      return next;
    });
    try {
      const response = await fetch(`${secretPath(targetSlug)}/usage`);
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || 'Failed to load secret usage');
      }
      const usage = (await response.json()) as SecretUsageResponse;
      setUsageBySlug((current) => ({ ...current, [targetSlug]: usage }));
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to load secret usage';
      setUsageErrorBySlug((current) => ({ ...current, [targetSlug]: message }));
      onNotice({ level: 'error', text: message });
    } finally {
      setUsageLoadingSlug(null);
    }
  }, [onNotice]);

  useEffect(() => {
    for (const secret of secrets) {
      const identity = `${secret.slug}:${secret.status}`;
      if (!isBrokenReferenceStatus(secret.status) || secret.usages || usageBySlug[secret.slug] ||
          automaticUsageAttempts.current.has(identity)) continue;
      automaticUsageAttempts.current.add(identity);
      void loadUsage(secret.slug);
    }
  }, [secrets, usageBySlug, loadUsage]);

  const renderUsages = (secret: SecretMetadata) => {
    const loadedUsage = usageBySlug[secret.slug];
    const usages = loadedUsage?.usages ?? secret.usages ?? [];
    const usageError = usageErrorBySlug[secret.slug];
    const usageLoaded = Boolean(loadedUsage || secret.usages);
    const isBroken = isBrokenReferenceStatus(secret.status);

    if (usageError) {
      return <span className="text-xs text-red-600 dark:text-red-400">{usageError}</span>;
    }

    if (!usageLoaded) {
      return (
        <Button variant="ghost" size="sm"
          type="button"
          onClick={() => loadUsage(secret.slug)}
          disabled={usageLoadingSlug === secret.slug}
        >
          {usageLoadingSlug === secret.slug ? 'Loading...' : 'View usage'}
        </Button>
      );
    }

    if (usages.length === 0) {
      return <span className="text-xs text-slate-500 dark:text-slate-400">No consumers</span>;
    }

    return (
      <ul className="space-y-2">
        {usages.map((usage) => (
          <li key={`${usage.consumerType}:${usage.objectName}:${usage.reference}`}>
            <div className="flex flex-wrap items-center gap-2">
              <div className="text-xs font-medium text-slate-700 dark:text-slate-200">
                {usage.objectName}
              </div>
              {isBroken ? (
                <span
                  className="badge badge-error"
                  aria-label={`Broken reference for ${usage.objectName}`}
                  title="Launches that bind this secret are blocked until it is active."
                >
                  Broken reference
                </span>
              ) : null}
            </div>
            <code className="mt-1 inline-block rounded bg-slate-100 px-2 py-1 text-xs text-slate-800 dark:bg-slate-900 dark:text-slate-100">
              {usage.reference}
            </code>
          </li>
        ))}
      </ul>
    );
  };

  const secretActionItems = (secret: SecretMetadata): WorkflowActionMenuItem[] => [
    { id: 'edit', label: 'Edit', onSelect: () => editSecret(secret.slug) },
    {
      id: 'rotate',
      label: 'Rotate',
      onSelect: () => {
        setRotatePromptSlug(secret.slug);
        setRotatePromptVal('');
        setFormError(null);
        setRotatePromptOpen(true);
      },
    },
    {
      id: 'delete',
      label: 'Delete',
      danger: true,
      section: 'destructive',
      confirmation: {
        title: `Delete ${secret.slug}?`,
        consequence: 'The stored value is removed. Anything that references this secret stops working.',
        confirmLabel: 'Delete secret',
      },
      onSelect: () => deleteOp.mutate(secret.slug),
    },
  ];

  return (
    <section
      aria-labelledby="managed-secrets-heading"
      className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3 pb-3">
        <div className="min-w-0 space-y-1">
          <h3 id="managed-secrets-heading" className="m-0 text-lg font-semibold text-slate-900 dark:text-white">Managed Secrets</h3>
          <p className="m-0 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Encrypted values that provider profiles reference as <code>db://SLUG</code>.
          </p>
        </div>
        <Button variant="secondary" type="button" className="text-sm" onClick={addSecret}>
          Add secret
        </Button>
      </div>

      <div className="managed-secrets-table-wrap stacked-records-wrap overflow-x-auto">
        <table className="managed-secrets-table settings-flat-table stacked-records w-full text-left text-sm">
          <thead>
            <tr>
              <th className="px-3 py-2 text-xs font-medium text-slate-600 dark:text-slate-400">Secret</th>
              <th className="px-3 py-2 text-xs font-medium text-slate-600 dark:text-slate-400">Used by</th>
              <th className="px-3 py-2 text-xs font-medium text-slate-600 dark:text-slate-400">Status</th>
              <th className="px-3 py-2 text-xs font-medium text-slate-600 dark:text-slate-400">Updated</th>
              <th className="px-3 py-2 text-xs font-medium text-slate-600 dark:text-slate-400">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {secrets.length === 0 ? (
              <tr>
                <td className="px-3 py-6 text-slate-500 dark:text-slate-400" colSpan={5}>
                  No secrets currently stored.
                </td>
              </tr>
            ) : (
              secrets.map((secret) => {
                const secretRef = secret.secretRef ?? `db://${secret.slug}`;
                const updated = new Date(secret.updatedAt ?? secret.createdAt);
                return (
                  <tr key={secret.slug} id={`managed-secret-${secret.slug}`} tabIndex={-1}>
                    <td data-label="Secret" className="px-3 py-2.5">
                      <div className="flex min-w-0 items-center gap-1">
                        <span className="min-w-0 font-mono font-semibold text-slate-900 dark:text-white [overflow-wrap:anywhere]">
                          {secret.slug}
                        </span>
                        <Button
                          variant="icon"
                          size="sm"
                          type="button"
                          className="shrink-0"
                          onClick={() => copySecretRef(secretRef)}
                          aria-label={`Copy SecretRef for ${secret.slug}`}
                          title={`Copy ${secretRef}`}
                        >
                          {COPY_ICON}
                        </Button>
                      </div>
                      {secretRef !== `db://${secret.slug}` ? (
                        <code className="text-xs text-slate-500 dark:text-slate-400 [overflow-wrap:anywhere]">{secretRef}</code>
                      ) : null}
                    </td>
                    <td data-label="Used by" className="px-3 py-2.5">{renderUsages(secret)}</td>
                    <td data-label="Status" className="px-3 py-2.5">
                      <div className="flex flex-wrap items-center gap-2">
                        {renderStatus(secret.status)}
                        {/* The one-step fix stays in the row, where a "Show secret" link lands. */}
                        {secret.status === 'disabled' ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            type="button"
                            onClick={() => setStatusOp.mutate({ slug: secret.slug, status: 'active' })}
                            disabled={setStatusOp.isPending}
                            aria-label={`Re-enable ${secret.slug}`}
                          >
                            Re-enable
                          </Button>
                        ) : null}
                      </div>
                    </td>
                    <td data-label="Updated" className="px-3 py-2.5 text-xs text-slate-500 dark:text-slate-400">
                      <time dateTime={updated.toISOString()} title={updated.toLocaleString()}>
                        {formatShortDate(updated)}
                      </time>
                    </td>
                    <td data-label="Actions" className="px-3 py-2.5">
                      <div className="managed-secrets-actions">
                        <WorkflowActionsMenu
                          items={secretActionItems(secret)}
                          triggerContent={MORE_ACTIONS_ICON}
                          triggerClassName="td-workflow-actions-trigger-compact"
                          triggerAriaLabel={`Actions for ${secret.slug}`}
                          menuAriaLabel={`${secret.slug} actions`}
                          emptyMessage="No actions are available."
                          subject={secret.slug}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {isAdding || isEditing ? (
        <SecretDialog
          titleId="managed-secret-editor-title"
          title={isEditing ? `Update secret value: ${slug}` : 'Add secret'}
          error={formError}
          onClose={closeSecretDialog}
        >
          <form className="stack mt-4" onSubmit={handleSubmit} noValidate>
            <div className="field">
              <label htmlFor="secSlug" className="text-slate-700 dark:text-slate-300">Secret slug</label>
              <input
                id="secSlug"
                type="text"
                placeholder="e.g. OPENAI_API_KEY"
                className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                value={slug}
                onChange={(event) => setSlug(event.target.value)}
                disabled={isEditing || createOp.isPending || updateOp.isPending}
              />
              <div className="field-hint text-slate-500 dark:text-slate-400">The unique locator used by the `db://` resolver.</div>
            </div>

            <div className="field mt-4">
              <label htmlFor="secPlaintext" className="text-slate-700 dark:text-slate-300">Secure value</label>
              <input
                id="secPlaintext"
                type="password"
                placeholder={isEditing ? '••••••••' : 'Enter raw secret string'}
                className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                value={plaintext}
                onChange={(event) => setPlaintext(event.target.value)}
                disabled={createOp.isPending || updateOp.isPending}
              />
              <div className="field-hint text-slate-500 dark:text-slate-400">
                Stored strictly as ciphertext. The raw value is never rendered back.
              </div>
            </div>

            <div className="mt-6 flex flex-wrap justify-end gap-2">
              <Button variant="ghost" type="button" onClick={closeSecretDialog}>
                Cancel
              </Button>
              <Button variant="primary"
                type="submit"
                className="inline-flex items-center justify-center text-sm"
                disabled={createOp.isPending || updateOp.isPending}
              >
                {isEditing ? 'Save new value' : 'Encrypt and store secret'}
              </Button>
            </div>
          </form>
        </SecretDialog>
      ) : null}

      {rotatePromptOpen ? (
        <SecretDialog
          titleId="managed-secret-rotate-title"
          title={`Rotate Secret: ${rotatePromptSlug}`}
          error={formError}
          onClose={closeSecretDialog}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!rotatePromptVal) {
                setFormError('Provide a new secure value to rotate the secret.');
                return;
              }
              rotateOp.mutate({
                slug: rotatePromptSlug,
                plaintext: rotatePromptVal,
              });
            }}
            className="mt-4 space-y-4"
            noValidate
          >
            <div className="space-y-2">
              <label htmlFor="secRotateValue" className="text-sm font-medium text-slate-700 dark:text-slate-300">New secure value</label>
              <input
                id="secRotateValue"
                type="password"
                className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                value={rotatePromptVal}
                onChange={(event) => setRotatePromptVal(event.target.value)}
                disabled={rotateOp.isPending}
              />
            </div>
            <div className="mt-6 flex flex-wrap justify-end gap-2">
              <Button variant="ghost" type="button" onClick={closeSecretDialog}>
                Cancel
              </Button>
              <Button variant="primary"
                type="submit"
                className="inline-flex items-center justify-center text-sm"
                disabled={rotateOp.isPending}
              >
                Rotate now
              </Button>
            </div>
          </form>
        </SecretDialog>
      ) : null}
    </section>
  );
}
