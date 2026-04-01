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
    <div className="card mt-4" style={{ backgroundColor: '#fdfdfd', border: '1px solid #e0e0e0', padding: '1rem', borderRadius: '8px' }}>
      <h3 style={{ marginTop: 0, fontSize: '1rem' }}>Agent Skill Provenance</h3>
      <dl className="queue-card-fields" style={{ marginBottom: 0 }}>
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
              <code className="small" style={{ wordBreak: 'break-all' }}>{resolvedSkillsetRef}</code>
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
