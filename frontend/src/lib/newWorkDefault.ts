import type { ProviderProfile } from '../components/settings/ProviderProfilesManager';

export type NewWorkDefaultProfile = Pick<ProviderProfile, 'profile_id'> &
  Partial<Pick<ProviderProfile, 'runtime_id' | 'enabled' | 'is_default' | 'priority' | 'readiness'>>;

export interface NewWorkDefaults {
  runtime: string | null;
  profileRef: string | null;
}

export interface NewWorkDefaultProblem {
  message: string;
  action: 'Change default' | 'Add profile';
}

/** Mirrors the launch path's explicit profile, then preferred/priority/ID fallback. */
export function newWorkDefaultProblem(
  defaults: NewWorkDefaults,
  profiles: NewWorkDefaultProfile[],
): NewWorkDefaultProblem | null {
  let selected: NewWorkDefaultProfile | undefined;
  if (defaults.profileRef) {
    selected = profiles.find((profile) => profile.profile_id === defaults.profileRef);
    if (!selected || !selected.enabled) {
      return {
        message: `New work defaults to ${defaults.profileRef}, which is ${selected ? 'disabled' : 'missing'}.`,
        action: 'Change default',
      };
    }
  } else if (defaults.runtime === 'codex_cli' || defaults.runtime === 'claude_code') {
    selected = profiles.filter((profile) => profile.enabled && profile.runtime_id === defaults.runtime)
      .sort((left, right) => Number(Boolean(right.is_default)) - Number(Boolean(left.is_default)) ||
        (right.priority ?? 0) - (left.priority ?? 0) ||
        (left.profile_id < right.profile_id ? -1 : left.profile_id > right.profile_id ? 1 : 0))[0];
    if (!selected) {
      return {
        message: `New work defaults to ${defaults.runtime === 'codex_cli' ? 'Codex' : 'Claude Code'}, which has no enabled profile.`,
        action: 'Add profile',
      };
    }
  }
  if (selected?.readiness?.status === 'blocked') {
    return {
      message: `New work defaults to ${selected.profile_id}, which is not launch-ready: ${selected.readiness.summary}`,
      action: 'Change default',
    };
  }
  return null;
}
