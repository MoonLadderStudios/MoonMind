type SkillProvenanceBadgeProps = {
  resolvedSkillsetRef?: string | null | undefined;
  taskSkills?: string[] | null | undefined;
  targetSkill?: string | null | undefined;
};

export function SkillProvenanceBadge({
  resolvedSkillsetRef,
  taskSkills,
  targetSkill,
}: SkillProvenanceBadgeProps) {
  const hasExplicitSkills = Array.isArray(taskSkills) && taskSkills.length > 0;
  
  
  return (
    <div className="card mt-4 bg-gray-50 border border-gray-200 p-4 rounded-lg">
      <h3 className="mt-0 text-base">Agent Skill Provenance</h3>
      <dl className="queue-card-fields mb-0">
        <div>
          <dt>Explicit Selection</dt>
          <dd>{hasExplicitSkills ? taskSkills.join(', ') : 'None'}</dd>
        </div>
        <div>
          <dt>Delegated Skill</dt>
          <dd>{targetSkill || '—'}</dd>
        </div>
        <div>
          <dt>Resolved Snapshot Ref</dt>
          <dd>
            {resolvedSkillsetRef ? (
              <code className="small break-all">{resolvedSkillsetRef}</code>
            ) : (
              <span className="small text-muted">No snapshot bound for this run.</span>
            )}
          </dd>
        </div>
      </dl>
      {!resolvedSkillsetRef && (
        <p className="small text-muted mt-2 mb-0">
          * Without a snapshot ref, the worker invokes standard legacy tools.
        </p>
      )}
    </div>
  );
}
