import { useQuery } from '@tanstack/react-query';
import { useRef } from 'react';
import { responseError } from '../../lib/api/client';
import type { ModelSelectionProfile } from '../../lib/modelSelection';
import {
  fetchProviderProfileTierCapabilities,
  fetchProviderProfileTierPreviews,
} from '../../lib/providerProfileTierPreviews';
import type { ScheduleAgentDraft } from '../../lib/scheduleAgentSelection';
import { formatHarnessLabel } from '../../runtime/runtimeTargets';
import { ModelSelectionFields } from './ModelSelectionFields';

type ScheduleProviderProfile = ModelSelectionProfile & {
  profile_id: string;
  runtime_id?: string | null;
  account_label?: string | null;
  provider_label?: string | null;
  is_default?: boolean;
  enabled?: boolean;
  launch_ready?: boolean;
};

async function previewErrorMessage(response: Response, fallback: string): Promise<string> {
  return (await responseError(response, fallback)).message;
}

/**
 * Harness, Provider Profile and model selection for a schedule's future runs,
 * with the same vocabulary and model interaction as Create. The Harness only
 * narrows the profile list: the profile decides the account, and the server
 * picks the Backend that profile runs on when the schedule is saved.
 */
export function ScheduleAgentSelectionFields({ providerProfilesEndpoint, value, onChange, error }: {
  providerProfilesEndpoint: string;
  value: ScheduleAgentDraft;
  onChange: (value: ScheduleAgentDraft) => void;
  error?: string | undefined;
}) {
  // A profile set aside by a Harness change returns when its Harness does.
  const setAside = useRef<Record<string, string>>({});
  const profilesQuery = useQuery({
    queryKey: ['schedule-agent', 'provider-profiles', providerProfilesEndpoint],
    queryFn: async (): Promise<ScheduleProviderProfile[]> => {
      const response = await fetch(providerProfilesEndpoint, { headers: { Accept: 'application/json' }, credentials: 'include' });
      if (!response.ok) throw await responseError(response, 'Failed to load provider profiles');
      return await response.json() as ScheduleProviderProfile[];
    },
  });
  const profiles = profilesQuery.data || [];
  const selected = profiles.find((profile) => profile.profile_id === value.profileId);
  const harness = value.harness || selected?.runtime_id || '';
  const harnessOptions = Array.from(new Set(
    [...profiles.map((profile) => profile.runtime_id || ''), harness].filter(Boolean),
  ));
  const profileOptions = profiles
    .filter((profile) => !harness || !profile.runtime_id || profile.runtime_id === harness)
    .sort((left, right) => Number(Boolean(right.is_default)) - Number(Boolean(left.is_default))
      || (left.account_label || left.profile_id).localeCompare(right.account_label || right.profile_id));
  const unavailable = Boolean(value.profileId) && profilesQuery.isSuccess && !selected;

  const selectionKey = JSON.stringify(value.selection);
  const previewQuery = useQuery({
    queryKey: ['schedule-agent', 'tier-preview', providerProfilesEndpoint, value.profileId, selectionKey, profilesQuery.dataUpdatedAt],
    enabled: Boolean(selected),
    queryFn: () => fetchProviderProfileTierPreviews(
      providerProfilesEndpoint,
      [{ id: 'schedule', profileId: value.profileId, runtime: value.selection }],
      previewErrorMessage,
    ),
  });
  const capabilitiesQuery = useQuery({
    queryKey: ['schedule-agent', 'capabilities', providerProfilesEndpoint, value.profileId],
    enabled: Boolean(selected),
    queryFn: ({ signal }) => fetchProviderProfileTierCapabilities(providerProfilesEndpoint, value.profileId, signal),
  });

  const selectHarness = (next: string) => {
    if (selected?.runtime_id) setAside.current[selected.runtime_id] = selected.profile_id;
    // A profile missing from the inventory has no known Harness to compare.
    const kept = value.profileId && (!selected?.runtime_id || selected.runtime_id === next);
    onChange({
      ...value,
      harness: next,
      // Another account is never chosen silently: it changes who is billed.
      profileId: kept ? value.profileId : setAside.current[next] || '',
    });
  };

  return <fieldset className="schedules-agent-selection" aria-label="Agent for future runs">
    <div className="schedules-agent-selection__identity">
      <label>
        <span>Harness</span>
        <select aria-label="Schedule Harness" value={harness} onChange={(event) => selectHarness(event.currentTarget.value)}>
          {!harness ? <option value="" disabled>{profilesQuery.isPending ? 'Loading Harness choices…' : 'Choose a Harness'}</option> : null}
          {harnessOptions.map((option) => <option key={option} value={option}>{formatHarnessLabel(option)}</option>)}
        </select>
      </label>
      <label>
        <span>Provider Profile</span>
        <select
          aria-label="Schedule Provider Profile"
          value={value.profileId}
          disabled={profilesQuery.isPending}
          aria-invalid={Boolean(error)}
          onChange={(event) => onChange({ ...value, profileId: event.currentTarget.value, harness: '' })}
        >
          {!value.profileId ? <option value="" disabled>
            {profilesQuery.isPending ? 'Loading profiles…' : harness ? `Choose a ${formatHarnessLabel(harness)} Provider Profile` : 'Choose a Provider Profile'}
          </option> : null}
          {value.profileId && !selected ? <option value={value.profileId} disabled>
            {unavailable ? `${value.profileId} (Unavailable — replacement required)` : value.profileId}
          </option> : null}
          {profileOptions.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>
            {profile.is_default && profile.provider_label ? `${profile.provider_label} (Default)` : profile.account_label || profile.profile_id}
            {profile.enabled === false ? ' · Disabled' : profile.launch_ready === false ? ' · Needs setup' : ''}
          </option>)}
        </select>
      </label>
    </div>
    {error ? <span className="schedules-field-error" role="alert">{error}</span> : null}
    {profilesQuery.isError ? <span className="schedules-field-error" role="alert">Failed to load provider profiles. The saved selection is unchanged.</span>
      : harness && profilesQuery.isSuccess && profileOptions.length === 0 ? <span className="schedules-field-error" role="alert">
        No Provider Profiles are configured for {formatHarnessLabel(harness)}. Configure one in Settings or choose another Harness.
      </span> : null}
    <ModelSelectionFields
      scope="Schedule"
      value={value.selection}
      profile={selected}
      capabilities={capabilitiesQuery.data?.capabilities}
      loading={profilesQuery.isPending}
      error={previewQuery.data?.errors.schedule || capabilitiesQuery.data?.error || (profilesQuery.isError ? 'Profile tiers could not be loaded.' : null)}
      preview={previewQuery.data?.previews.schedule}
      onChange={(selection) => onChange({ ...value, selection })}
    />
    <p className="page-meta">Applies to future runs. Runs that already started keep the agent they launched with.</p>
  </fieldset>;
}
