import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '../Button';
import { WorkflowActionsMenu } from '../WorkflowActionsMenu';
import { useDashboardToast } from '../dashboard/DashboardToast';
import { ApiError, responseError } from '../../lib/api/client';
import {
  canReset, displayValue, fetchCatalog, fetchSettingAudit, ProviderProfilePicker,
  providerProfileOptionLabel, SettingAuditHistory, valuesEqual,
  type SettingDescriptor,
} from './GeneratedSettingsSection';
import { fetchProviderProfiles, PROVIDER_PROFILE_QUERY_KEY } from './ProviderProfilesManager';
import { useSettingsDraftRegistration } from './SettingsDraftGuard';

const ROWS = [
  { key: 'workflow.default_runtime', title: 'Harness' },
  { key: 'workflow.default_provider_profile_ref', title: 'Provider profile' },
];
type RowMessages = Record<string, string[]>;
interface DraftValue { value: unknown; originalValue: unknown; valueVersion: number }
interface ValidationIssue { key?: string; message: string }
interface ValidationResult { accepted: boolean; issues_by_key: Record<string, ValidationIssue[]> }
interface SettingFailure { key?: string; message?: string; details?: { issues?: ValidationIssue[] } }

export function NewWorkDefaultsCard() {
  const queryClient = useQueryClient();
  const toast = useDashboardToast();
  const catalogQuery = useQuery({ queryKey: ['settings-catalog'], queryFn: fetchCatalog });
  const refetchCatalog = catalogQuery.refetch;
  const profilesQuery = useQuery({ queryKey: PROVIDER_PROFILE_QUERY_KEY, queryFn: fetchProviderProfiles });
  const [draft, setDraft] = useState<Record<string, DraftValue>>({});
  const [validation, setValidation] = useState<{ signature: string; issues: RowMessages } | null>(null);
  const [errors, setErrors] = useState<RowMessages>({});
  const [conflicts, setConflicts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [auditKey, setAuditKey] = useState<string | null>(null);
  const auditQuery = useQuery({
    queryKey: ['settings-audit', auditKey], queryFn: () => fetchSettingAudit(auditKey ?? ''), enabled: auditKey !== null,
  });
  const descriptors = useMemo(() => {
    const all = Object.values(catalogQuery.data?.categories ?? {}).flat();
    return ROWS.flatMap((row) => {
      const descriptor = all.find((item) => item.key === row.key);
      return descriptor ? [{ ...descriptor, title: row.title }] : [];
    });
  }, [catalogQuery.data]);
  const changed = useMemo(() => descriptors.filter((d) =>
    Object.hasOwn(draft, d.key) && !valuesEqual(draft[d.key]!.value, draft[d.key]!.originalValue)), [descriptors, draft]);
  const proposal = useMemo(() => ({
    changes: Object.fromEntries(changed.map((d) => [d.key, draft[d.key]!.value])),
    expected_versions: Object.fromEntries(changed.map((d) => [d.key, draft[d.key]!.valueVersion])),
  }), [changed, draft]);
  const signature = JSON.stringify(proposal);
  const dirty = changed.length > 0;
  const discard = useCallback(() => { setDraft({}); setErrors({}); setConflicts({}); setValidation(null); }, []);
  useSettingsDraftRegistration('new-work-defaults', dirty, discard);

  const valueLabel = useCallback((descriptor: SettingDescriptor, value: unknown) => {
    const option = descriptor.options?.find((item) => valuesEqual(item.value, value));
    if (option) return option.label;
    if (descriptor.ui === 'provider_profile_picker' && typeof value === 'string' && value) {
      const profile = profilesQuery.data?.find((item) => item.profile_id === value);
      return profile ? providerProfileOptionLabel(profile) : `${value}${profilesQuery.isSuccess ? ' (not found)' : ''}`;
    }
    return displayValue(value);
  }, [profilesQuery.data, profilesQuery.isSuccess]);

  const handleFailure = useCallback(async (error: unknown, keys: string[], validationSignature?: string) => {
    const message = error instanceof Error ? error.message : 'Settings request failed';
    if (error instanceof ApiError) {
      const failure = (error.detail ?? {}) as SettingFailure;
      let affected = failure.key ? [failure.key] : keys;
      if (error.status === 409) {
        const refreshed = await refetchCatalog();
        if (refreshed.isError) { toast.error({ title: refreshed.error.message }); return; }
        const all = Object.values(refreshed.data?.categories ?? {}).flat();
        if (!failure.key) {
          const stale = keys.filter((key) =>
            all.find((descriptor) => descriptor.key === key)?.value_version !== proposal.expected_versions[key]);
          if (stale.length) affected = stale;
        }
        setDraft((current) => {
          const next = { ...current };
          for (const key of affected) {
            const descriptor = all.find((d) => d.key === key);
            if (next[key] && descriptor) next[key] = { ...next[key], valueVersion: descriptor.value_version };
          }
          return next;
        });
        setConflicts((current) => ({ ...current, ...Object.fromEntries(affected.map((key) => {
          const descriptor = all.find((d) => d.key === key);
          return [key, `Changed elsewhere to ${descriptor ? valueLabel(descriptor, descriptor.effective_value) : 'an unavailable value'}. Save again to replace it, or cancel.`];
        })) }));
        return;
      }
      if (error.status === 400 || error.status === 423) {
        const issues = failure.details?.issues;
        const messages: RowMessages = {};
        for (const issue of issues ?? []) if (issue.key) (messages[issue.key] ??= []).push(issue.message);
        if (!Object.keys(messages).length) for (const key of affected) messages[key] = [message];
        if (validationSignature) setValidation({ signature: validationSignature, issues: messages });
        else setErrors((current) => ({ ...current, ...messages }));
        return;
      }
    }
    toast.error({ title: message });
  }, [refetchCatalog, proposal.expected_versions, toast, valueLabel]);
  useEffect(() => {
    if (!dirty) return;
    const controller = new AbortController();
    let current = true;
    const timer = setTimeout(async () => {
      try {
        const response = await fetch('/api/v1/settings/validate', {
          method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: signature, signal: controller.signal,
        });
        if (!response.ok) throw await responseError(response);
        const result = await response.json() as ValidationResult;
        if (current) setValidation({ signature, issues: Object.fromEntries(Object.entries(result.issues_by_key ?? {})
          .map(([key, issues]) => [key, issues.map((issue) => issue.message)])) });
      } catch (error) {
        if (!current) return;
        await handleFailure(error, Object.keys(proposal.changes), signature);
      }
    }, 300);
    return () => { current = false; clearTimeout(timer); controller.abort(); };
  }, [dirty, signature, proposal.changes, handleFailure]);

  const save = async () => {
    if (!dirty || busy) return;
    if (validation?.signature === signature && Object.values(validation.issues).some((issues) => issues.length)) return;
    setBusy(true);
    try {
      const response = await fetch('/api/v1/settings', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ ...proposal, reason: 'Updated from Providers & Secrets.' }),
      });
      if (!response.ok) throw await responseError(response);
      discard();
      await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] });
      toast.success({ title: 'Defaults saved' });
    } catch (error) { await handleFailure(error, changed.map((d) => d.key)); }
    finally { setBusy(false); }
  };
  const reset = async (descriptor: SettingDescriptor) => {
    setBusy(true);
    try {
      const response = await fetch(`/api/v1/settings/${encodeURIComponent(descriptor.key)}`, { method: 'DELETE', headers: { Accept: 'application/json' } });
      if (!response.ok) throw await responseError(response);
      updateDraft(descriptor, descriptor.effective_value, true);
      await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] });
      toast.success({ title: `${descriptor.title} reset to default` });
    } catch (error) { await handleFailure(error, [descriptor.key]); }
    finally { setBusy(false); }
  };
  const updateDraft = (descriptor: SettingDescriptor, value: unknown, remove = false) => {
    setDraft((current) => {
      const next = { ...current };
      const baseline = current[descriptor.key] ?? {
        originalValue: descriptor.effective_value, valueVersion: descriptor.value_version,
      };
      if (remove || valuesEqual(value, baseline.originalValue)) delete next[descriptor.key];
      else next[descriptor.key] = { ...baseline, value };
      return next;
    });
    setErrors((current) => ({ ...current, [descriptor.key]: [] }));
    setConflicts((current) => { const next = { ...current }; delete next[descriptor.key]; return next; });
  };
  const cardIssues = Object.entries(validation?.signature === signature ? validation.issues : {}).concat(Object.entries(errors))
    .flatMap(([key, messages]) => ROWS.some((row) => row.key === key) ? [] : messages.map((message) => ({ key, message })));

  return (
    <section id="new-work-defaults" tabIndex={-1} aria-label="Defaults for new work"
      className="rounded-3xl border border-mm-border/80 p-5 shadow-sm space-y-4">
      <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Defaults for new work</h3>
      <p className="text-sm text-slate-600 dark:text-slate-400">Used when a launch doesn&apos;t choose a harness or profile.</p>
      {cardIssues.map(({ key, message }, index) => <p role="alert" key={`${key}-${index}`} className="text-sm text-rose-700 dark:text-rose-400">
        {key}: {message} <a href="/settings/instance" className="underline">View Instance settings</a>
      </p>)}
      {catalogQuery.isLoading ? <p role="status">Loading defaults…</p> : catalogQuery.isError ? <p role="alert">{catalogQuery.error.message}</p> :
        descriptors.map((descriptor) => {
          const value = Object.hasOwn(draft, descriptor.key) ? draft[descriptor.key]!.value : descriptor.effective_value;
          const issues = [...(validation?.signature === signature ? validation.issues[descriptor.key] ?? [] : []), ...(errors[descriptor.key] ?? [])];
          return <div key={descriptor.key} data-setting-row className="space-y-2">
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <label htmlFor={`new-work-${descriptor.key}`} className="text-sm font-medium">{descriptor.title}</label>
                {descriptor.ui === 'provider_profile_picker' ? <p className="text-xs text-slate-500">Takes priority over the harness when set.</p> : null}
              </div>
              <fieldset disabled={busy} className="min-w-0 max-w-full flex-1">
                {descriptor.read_only ? <><p>{valueLabel(descriptor, value)}</p><p className="text-xs text-slate-500">{descriptor.read_only_reason}</p></> :
                  descriptor.ui === 'provider_profile_picker' ? <ProviderProfilePicker descriptor={descriptor} id={`new-work-${descriptor.key}`} value={value}
                    onChange={(d, next) => updateDraft(d, next === '' ? null : next)} /> :
                    <select id={`new-work-${descriptor.key}`} aria-label={descriptor.title} value={String(value ?? '')}
                      className="w-full rounded-xl border border-mm-border bg-mm-surface px-3 py-2 text-sm"
                      onChange={(event) => updateDraft(descriptor, event.target.value)}>
                      {descriptor.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>}
              </fieldset>
              <WorkflowActionsMenu triggerContent="⋯" triggerAriaLabel={`${descriptor.title} actions`} items={[
                { id: 'history', label: 'View history', onSelect: () => setAuditKey(auditKey === descriptor.key ? null : descriptor.key) },
                ...(canReset(descriptor) ? [{ id: 'reset', label: 'Reset to default',
                  disabledReason: busy ? 'Saving defaults' : descriptor.read_only ? descriptor.read_only_reason || 'Read-only setting' : null,
                  onSelect: () => { void reset(descriptor); } }] : []),
              ]} />
            </div>
            {conflicts[descriptor.key] ? <p role="alert" className="text-sm text-amber-700 dark:text-amber-300">{conflicts[descriptor.key]}</p> : null}
            {issues.map((message, index) => <p role="alert" key={`${index}-${message}`} className="text-sm text-rose-700 dark:text-rose-400">{message}</p>)}
            {auditKey === descriptor.key ? <SettingAuditHistory descriptor={descriptor} query={auditQuery} /> : null}
          </div>;
        })}
      {dirty ? <div className="border-t border-mm-border pt-3" aria-label="Unsaved defaults">
        <ul className="space-y-1 text-sm">{changed.map((descriptor) => <li key={descriptor.key}>
          {descriptor.title}: {valueLabel(descriptor, draft[descriptor.key]!.originalValue)} → {valueLabel(descriptor, draft[descriptor.key]!.value)}
        </li>)}</ul>
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="ghost" disabled={busy} onClick={discard}>Cancel</Button>
          <Button disabled={busy} onClick={() => { void save(); }}>Save</Button>
        </div>
      </div> : null}
    </section>
  );
}
