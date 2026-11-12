import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '../Button';
import { useDashboardToast } from '../dashboard/DashboardToast';
import { ApiError, responseError } from '../../lib/api/client';
import {
  displayValue, fetchCatalog,
  providerProfileOptionLabel, valuesEqual,
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
  const catalogDescriptors = useMemo(() => Object.values(catalogQuery.data?.categories ?? {}).flat(), [catalogQuery.data]);
  const descriptors = useMemo(() => {
    return ROWS.flatMap((row) => {
      const descriptor = catalogDescriptors.find((item) => item.key === row.key);
      return descriptor ? [{ ...descriptor, title: row.title }] : [];
    });
  }, [catalogDescriptors]);
  const changed = useMemo(() => descriptors.filter((d) =>
    Object.hasOwn(draft, d.key) && !valuesEqual(draft[d.key]!.value, draft[d.key]!.originalValue)), [descriptors, draft]);
  // Version-check the whole selection, including a field whose value did not change.
  const proposal = useMemo(() => ({
    changes: Object.fromEntries(changed.filter((d) => !d.read_only).map((d) => [d.key, draft[d.key]!.value])),
    expected_versions: Object.fromEntries(descriptors.filter((d) => draft[d.key]).map((d) => [d.key, draft[d.key]!.valueVersion])),
  }), [changed, descriptors, draft]);
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

  const handleFailure = useCallback(async (error: unknown, keys: string[], validationSignature?: string, isCurrent: () => boolean = () => true) => {
    const message = error instanceof Error ? error.message : 'Settings request failed';
    if (error instanceof ApiError) {
      const failure = (error.detail ?? {}) as SettingFailure;
      let affected = failure.key ? [failure.key] : keys;
      if (error.status === 409) {
        const refreshed = await refetchCatalog();
        // A validation probe may outlive an edit or Cancel while it reloads.
        if (!isCurrent()) return;
        if (refreshed.isError) { toast.error({ title: refreshed.error.message }); return; }
        const all = Object.values(refreshed.data?.categories ?? {}).flat();
        if (!failure.key) {
          const stale = keys.filter((key) =>
            all.find((descriptor) => descriptor.key === key)?.value_version !== proposal.expected_versions[key]);
          if (stale.length) affected = stale;
        }
        setDraft((current) => {
          const next = { ...current };
          for (const key of keys) {
            const descriptor = all.find((d) => d.key === key);
            if (next[key] && descriptor) next[key] = { ...next[key], originalValue: descriptor.effective_value, valueVersion: descriptor.value_version };
          }
          return Object.values(next).every((entry) => valuesEqual(entry.value, entry.originalValue)) ? {} : next;
        });
        setConflicts((current) => ({ ...current, ...Object.fromEntries(affected.map((key) => {
          const descriptor = all.find((d) => d.key === key);
          const matchesSelection = descriptor && draft[key] && valuesEqual(draft[key].value, descriptor.effective_value);
          return [key, `Changed elsewhere to ${descriptor ? valueLabel(descriptor, descriptor.effective_value) : 'an unavailable value'}. ${matchesSelection ? 'Your selected value is already saved.' : 'Save again to replace it, or cancel.'}`];
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
  }, [refetchCatalog, proposal.expected_versions, draft, toast, valueLabel]);
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
        await handleFailure(error, Object.keys(proposal.expected_versions), signature, () => current);
      }
    }, 300);
    return () => { current = false; clearTimeout(timer); controller.abort(); };
  }, [dirty, signature, proposal.expected_versions, handleFailure]);

  const save = async () => {
    if (!dirty || busy || !pairAllowed(Object.fromEntries(descriptors.map((d) => [d.key, currentValue(d)])))) return;
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
    } catch (error) { await handleFailure(error, Object.keys(proposal.expected_versions)); }
    finally { setBusy(false); }
  };
  const runtime = descriptors.find((d) => d.key === ROWS[0]!.key);
  const profile = descriptors.find((d) => d.key === ROWS[1]!.key);
  const currentValue = (d: SettingDescriptor | undefined) => d && (Object.hasOwn(draft, d.key) ? draft[d.key]!.value : d.effective_value);
  const originalValue = (d: SettingDescriptor | undefined) => d && (Object.hasOwn(draft, d.key) ? draft[d.key]!.originalValue : d.effective_value);
  const savedRuntime = originalValue(runtime);
  const savedProfile = originalValue(profile);
  const runtimeValue = currentValue(runtime);
  const profileValue = currentValue(profile);
  const profiles = profilesQuery.data ?? [];
  const selectedProfile = profiles.find((item) => item.profile_id === profileValue);
  const mismatched = !!selectedProfile && selectedProfile.runtime_id !== runtimeValue;
  const selection = profileValue ? `${mismatched ? 'saved' : 'profile'}:${profileValue}` : `runtime:${runtimeValue ?? ''}`;
  const retainedProfile = profiles.find((item) => item.profile_id === savedProfile);
  const retainedMismatch = !!retainedProfile && retainedProfile.runtime_id !== savedRuntime;
  const retainedSelection = `${retainedMismatch ? 'saved' : 'profile'}:${savedProfile}`;
  const readOnly = descriptors.every((d) => d.read_only) || descriptors.length !== 2;
  const pairAllowed = (values: Record<string, unknown>) => descriptors.length === 2 && descriptors.every((d) =>
    !d.read_only || valuesEqual(values[d.key], d.effective_value));
  const optionDisabled = (runtimeValue: unknown, profileValue: unknown) => !runtime || !profile ||
    !pairAllowed({ [runtime.key]: runtimeValue, [profile.key]: profileValue });
  const updateSelection = (selection: string) => {
    if (busy || readOnly || !runtime || !profile) return;
    const separator = selection.indexOf(':');
    const kind = selection.slice(0, separator);
    const id = selection.slice(separator + 1);
    const nextProfile = profiles.find((item) => item.profile_id === id);
    const restoringSaved = selection === retainedSelection && (kind === 'saved' || !nextProfile);
    if (!restoringSaved && kind === 'profile' && !nextProfile) return;
    const values = restoringSaved ? { [runtime.key]: savedRuntime, [profile.key]: savedProfile } : { [runtime.key]: kind === 'profile' ? nextProfile!.runtime_id : id,
      [profile.key]: kind === 'profile' ? id : null };
    if (!pairAllowed(values)) return;
    setDraft((current) => {
      const next = Object.fromEntries(descriptors.map((descriptor) => [descriptor.key, {
        ...(current[descriptor.key] ?? { originalValue: descriptor.effective_value, valueVersion: descriptor.value_version }),
        value: values[descriptor.key],
      }]));
      return Object.values(next).every((entry) => valuesEqual(entry.value, entry.originalValue)) ? {} : next;
    });
    setErrors({}); setConflicts({}); setValidation(null);
  };
  const cardIssues = Object.entries(validation?.signature === signature ? validation.issues : {}).concat(Object.entries(errors))
    .flatMap(([key, messages]) => ROWS.some((row) => row.key === key) ? [] : messages.map((message) => ({ key, message })));
  const settingLabel = (key: string) => ROWS.find((row) => row.key === key)?.title
    ?? catalogDescriptors.find((descriptor) => descriptor.key === key)?.title ?? 'Other setting';
  const issueMessage = (key: string, message: string) => message.replaceAll(key, settingLabel(key));

  return (
    <section id="new-work-defaults" tabIndex={-1} aria-label="Defaults for new work"
      className="rounded-3xl border border-mm-border/80 p-5 shadow-sm space-y-4">
      <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Defaults for new work</h3>
      <p className="text-sm text-slate-600 dark:text-slate-400">Used when a launch doesn&apos;t choose a harness or profile.</p>
      {cardIssues.map(({ key, message }, index) => <p role="alert" key={`${key}-${index}`} className="text-sm text-rose-700 dark:text-rose-400">
        {settingLabel(key)}: {issueMessage(key, message)} <a href="/settings/instance" className="underline">View Instance settings</a>
      </p>)}
      {catalogQuery.isLoading ? <p role="status">Loading defaults…</p> : catalogQuery.isError ? <p role="alert">{catalogQuery.error.message}</p> :
        <div data-setting-row className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="new-work-default" className="w-full text-sm font-medium sm:w-auto">Default for new work</label>
            <select id="new-work-default" disabled={busy || readOnly} value={selection} onChange={(event) => updateSelection(event.target.value)}
              className="min-w-0 w-full flex-none rounded-xl border border-mm-border bg-mm-surface px-3 py-2 text-sm sm:w-auto sm:flex-1">
              {runtime?.options?.map((option) => <option key={option.value} value={`runtime:${option.value}`} disabled={optionDisabled(option.value, null)}>
                {option.label}{option.value === 'omnigent' ? ' (basic)' : ' (preferred profile)'}
              </option>)}
              {!runtime?.options?.some((option) => option.value === savedRuntime) ?
                <option value={`runtime:${savedRuntime ?? ''}`} disabled={optionDisabled(savedRuntime, null)}>{String(savedRuntime ?? 'Not set')} (saved custom harness)</option> : null}
              {profiles.map((item) => <option key={item.profile_id} value={`profile:${item.profile_id}`} disabled={optionDisabled(item.runtime_id, item.profile_id)}>{providerProfileOptionLabel(item)}</option>)}
              {savedProfile && (!retainedProfile || retainedMismatch) ? <option value={retainedSelection} disabled={optionDisabled(savedRuntime, savedProfile)}>
                {retainedProfile ? `${providerProfileOptionLabel(retainedProfile)} (saved; harness differs)` : `${savedProfile}${profilesQuery.isSuccess ? ' (not found)' : ''}`}
              </option> : null}
              {profileValue && (!selectedProfile || mismatched) && selection !== retainedSelection ? <option value={selection} disabled={optionDisabled(runtimeValue, profileValue)}>
                {selectedProfile ? `${providerProfileOptionLabel(selectedProfile)} (draft; harness differs)` : `${profileValue}${profilesQuery.isSuccess ? ' (not found)' : ''}`}
              </option> : null}
            </select>
            {dirty ? <><Button variant="ghost" disabled={busy} onClick={discard}>Cancel</Button>
              <Button disabled={busy || !pairAllowed(Object.fromEntries(descriptors.map((d) => [d.key, currentValue(d)])))} onClick={() => { void save(); }}>Save</Button></> : null}
          </div>
          {descriptors.filter((d) => d.read_only).map((d) => <p key={d.key} className="text-xs text-slate-500">{d.title}: {d.read_only_reason || 'Read-only setting'}</p>)}
          {mismatched ? <p className="text-sm text-slate-500">The saved profile takes priority over the saved harness. {optionDisabled(selectedProfile?.runtime_id, profileValue) ? 'The harness cannot be aligned while its setting is locked.' : 'Select its profile option to align the harness, then Save.'}</p> : null}
          {profilesQuery.isLoading ? <p role="status">Loading Provider Profiles…</p> : null}
          {profilesQuery.isError ? <p role="alert">{profilesQuery.error.message}</p> : null}
          {descriptors.map((descriptor) => <div key={descriptor.key}>
            {conflicts[descriptor.key] ? <p role="alert" className="text-sm text-amber-700 dark:text-amber-300">{descriptor.title}: {conflicts[descriptor.key]}</p> : null}
            {[...(validation?.signature === signature ? validation.issues[descriptor.key] ?? [] : []), ...(errors[descriptor.key] ?? [])]
              .map((message, index) => <p role="alert" key={`${index}-${message}`} className="text-sm text-rose-700 dark:text-rose-400">{issueMessage(descriptor.key, message)}</p>)}
          </div>)}
          <a href="/settings/instance" className="text-sm underline">Advanced default settings and reset</a>
        </div>}
      {dirty ? <div className="border-t border-mm-border pt-3" aria-label="Unsaved defaults">
        <ul className="space-y-1 text-sm">{changed.map((descriptor) => <li key={descriptor.key}>
          {descriptor.title}: {valueLabel(descriptor, draft[descriptor.key]!.originalValue)} → {valueLabel(descriptor, draft[descriptor.key]!.value)}
        </li>)}</ul>
      </div> : null}
    </section>
  );
}
