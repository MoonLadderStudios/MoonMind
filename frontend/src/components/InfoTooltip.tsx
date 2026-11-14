import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** Shared, keyboard- and touch-accessible contextual help. */
export function InfoTooltip({ label, panelId, title, className, children }: {
  label: string;
  panelId: string;
  title: string;
  className?: string;
  children: ReactNode;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [position, setPosition] = useState({ left: 8, top: 8 });
  const visible = (hovered || focused || pinned) && !dismissed;
  const enterTrigger = () => {
    clearTimeout(leaveTimer.current);
    clearTimeout(hoverTimer.current);
    // Brief pointer transit must not open help over the next form control.
    // Keyboard focus and an explicit click remain immediate.
    hoverTimer.current = setTimeout(() => {
      setHovered(true);
      setDismissed(false);
    }, 350);
  };
  const enterPanel = () => {
    clearTimeout(leaveTimer.current);
    setHovered(true);
    setDismissed(false);
  };
  const leave = () => {
    clearTimeout(hoverTimer.current);
    leaveTimer.current = setTimeout(() => setHovered(false), 120);
  };
  const dismiss = () => {
    clearTimeout(hoverTimer.current);
    clearTimeout(leaveTimer.current);
    setHovered(false);
    setPinned(false);
    setDismissed(true);
  };
  useEffect(() => () => { clearTimeout(hoverTimer.current); clearTimeout(leaveTimer.current); }, []);
  useEffect(() => {
    if (!visible) return;
    const outside = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !panel.current?.contains(event.target as Node)) {
        clearTimeout(hoverTimer.current);
        clearTimeout(leaveTimer.current);
        setHovered(false);
        setPinned(false);
        setDismissed(true);
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        clearTimeout(hoverTimer.current);
        clearTimeout(leaveTimer.current);
        setHovered(false);
        setPinned(false);
        setDismissed(true);
      }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [visible]);
  useLayoutEffect(() => {
    if (!visible) return;
    const reposition = () => {
      if (!trigger.current || !panel.current) return;
      const anchor = trigger.current.getBoundingClientRect();
      const box = panel.current.getBoundingClientRect();
      const left = Math.max(8, Math.min(anchor.left + anchor.width / 2 - box.width / 2, window.innerWidth - box.width - 8));
      // Prefer above the label so deliberate help does not cover the next
      // form control. Near the viewport top, fall back below and clamp.
      const above = anchor.top - box.height - 8;
      const top = above >= 8
        ? above : Math.max(8, Math.min(anchor.bottom + 8, window.innerHeight - box.height - 8));
      setPosition({ left, top });
    };
    reposition();
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    return () => {
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
    };
  }, [visible, children]);
  return <span className={`queue-info-tooltip${className ? ` ${className}` : ''}`}>
    <button ref={trigger} type="button" className="queue-step-icon-button queue-info-toggle"
      aria-label={label} aria-describedby={panelId} aria-expanded={visible} title={title}
      onMouseEnter={enterTrigger} onMouseLeave={leave}
      onFocus={() => { clearTimeout(hoverTimer.current); setFocused(true); setDismissed(false); }}
      onBlur={() => { setFocused(false); setPinned(false); }}
      onClick={() => {
        clearTimeout(hoverTimer.current);
        if (pinned) dismiss();
        else { setPinned(true); setDismissed(false); }
      }}>
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <circle cx="12" cy="12" r="9.5" strokeWidth="2" />
        <path d="M12 11v6.25" strokeWidth="2.6" strokeLinecap="round" />
        <circle cx="12" cy="7.75" r="1.45" fill="currentColor" stroke="none" />
      </svg>
    </button>
    {createPortal(<div ref={panel} id={panelId} role="tooltip" hidden={!visible}
      className="queue-info-tooltip-panel"
      style={position} onMouseEnter={enterPanel} onMouseLeave={leave}>
      {children}
    </div>, document.body)}
  </span>;
}
