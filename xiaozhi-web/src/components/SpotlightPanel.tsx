import { useRef, type ReactNode } from 'react';

/** Local lightweight implementation of a pointer spotlight; no animation runtime. */
export default function SpotlightPanel({ children, enabled }: { children: ReactNode; enabled: boolean }) {
  const panel = useRef<HTMLElement>(null);
  return <section ref={panel} className="welcome-panel tech-panel" onPointerMove={event => {
    if (!enabled || event.pointerType === 'touch' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const el = panel.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    el.style.setProperty('--spot-x', `${event.clientX - rect.left}px`);
    el.style.setProperty('--spot-y', `${event.clientY - rect.top}px`);
  }}><div className="panel-spotlight" aria-hidden="true" />{children}</section>;
}
