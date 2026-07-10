import type { ReactNode } from 'react';

export type EntityDetailFrameState = 'loading' | 'not-found' | 'permission' | 'error';

export type EntityDetailFrameProps = {
  entity: 'workflow' | 'recurring';
  context?: ReactNode;
  identityStatus?: ReactNode;
  actions?: ReactNode;
  facts?: ReactNode;
  tabs?: ReactNode;
  main: ReactNode;
  factsRail?: ReactNode;
  state?: EntityDetailFrameState;
  stateContent?: ReactNode;
  factsRailLabel?: string;
};

/**
 * Entity-neutral detail composition shared by Workflow and Recurring routes.
 * Data fetching and entity capabilities deliberately remain in the adapters.
 */
export function EntityDetailFrame({
  entity,
  context,
  identityStatus,
  actions,
  facts,
  tabs,
  main,
  factsRail,
  state,
  stateContent,
  factsRailLabel = 'Details',
}: EntityDetailFrameProps) {
  return (
    <div className="entity-detail-frame" data-entity-detail-frame={entity}>
      {context ? <div className="entity-detail-frame__context">{context}</div> : null}
      {identityStatus || actions ? (
        <header className="entity-detail-frame__header">
          {identityStatus ? <div className="entity-detail-frame__identity">{identityStatus}</div> : null}
          {actions ? <div className="entity-detail-frame__actions">{actions}</div> : null}
        </header>
      ) : null}
      {facts ? <div className="entity-detail-frame__summary">{facts}</div> : null}
      {tabs ? (
        <nav className="entity-detail-frame__tabs" aria-label={`${entity} detail sections`}>
          {tabs}
        </nav>
      ) : null}
      <div className="entity-detail-frame__body">
        <div className="entity-detail-frame__main">
          {state ? (
            <div
              className={`entity-detail-frame__state entity-detail-frame__state--${state}`}
              role={state === 'loading' ? 'status' : 'alert'}
            >
              {stateContent}
            </div>
          ) : main}
        </div>
        {factsRail ? (
          <aside className="entity-detail-frame__facts" aria-label={factsRailLabel}>
            <div className="entity-detail-frame__facts-content">{factsRail}</div>
          </aside>
        ) : null}
      </div>
    </div>
  );
}
