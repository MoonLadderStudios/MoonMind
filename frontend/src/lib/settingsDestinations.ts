/** Shared ownership for generated controls and validation recovery links. */
export const NEW_WORK_DEFAULT_KEYS = ['workflow.default_runtime', 'workflow.default_publish_mode', 'workflow.default_provider_profile_ref'] as const;

export function settingDestination(key: string): { href: string; label: string } {
  if (key === 'security.assessment_projects') return { href: '/security/targets#assessment-projects', label: 'View assessment targets' };
  if (key === 'security.assessment_authorizations') return { href: '/security/targets#assessment-authorizations', label: 'View assessment authorizations' };
  return (NEW_WORK_DEFAULT_KEYS as readonly string[]).includes(key)
    ? { href: '/settings/providers-secrets#new-work-defaults-advanced', label: 'View advanced new work defaults' }
    : { href: '/settings/operations#advanced', label: 'View advanced operations settings' };
}
