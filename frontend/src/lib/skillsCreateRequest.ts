export const SKILLS_CREATE_REQUEST_EVENT = 'moonmind:skills-create-request';

// The "Create New Skill" affordance lives in the masthead nav (to the right of
// the System menu) while the create/upload drawer it opens is owned by the
// Skills page. They render in separate component subtrees, so the nav button
// asks the page to open its drawer through this window event — mirroring the
// other cross-component nav requests (workflow-start route guard, recurring
// focus).
export function requestSkillsCreate(): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new CustomEvent(SKILLS_CREATE_REQUEST_EVENT));
}
