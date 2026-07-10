import type { ReactNode } from 'react';

export function EntityDetailFrame({ entity, children }: {
  entity: 'workflow' | 'recurring';
  children: ReactNode;
}) {
  return <div className="entity-detail-frame" data-entity-detail-frame={entity}>{children}</div>;
}
