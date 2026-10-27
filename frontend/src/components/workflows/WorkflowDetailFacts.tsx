import { useEffect, useRef, useState, type ReactNode } from 'react';

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function FlatFactGrid({ children }: { children: ReactNode }) {
  return <dl className="flat-fact-grid">{children}</dl>;
}

export function FactGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="td-group">
      <h4>{title}</h4>
      <FlatFactGrid>{children}</FlatFactGrid>
    </div>
  );
}

/**
 * Collapsed by default: raw identifiers, digests, refs and detailed provenance
 * stay one click away. Children render only while open so closed details never
 * compete with the operational facts shown above them, or fetch their data.
 * A disclosure with an `anchorId` starts open, scrolled into view, when the URL
 * fragment names it, so other surfaces can link straight to its contents.
 */
export function TechnicalDetailsDisclosure({
  summary = 'Technical details',
  anchorId,
  children,
}: {
  summary?: string;
  anchorId?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(
    () => Boolean(anchorId) && window.location.hash === `#${anchorId}`,
  );
  const detailsRef = useRef<HTMLDetailsElement | null>(null);
  const openedFromAnchor = useRef(open);
  useEffect(() => {
    if (openedFromAnchor.current) {
      detailsRef.current?.scrollIntoView?.({ block: 'start' });
    }
  }, []);
  return (
    <details ref={detailsRef} id={anchorId} className="td-technical-details" open={open}>
      <summary
        onClick={(event) => {
          event.preventDefault();
          setOpen((current) => !current);
        }}
      >
        {summary}
      </summary>
      {open ? <div className="td-technical-details__body">{children}</div> : null}
    </details>
  );
}
