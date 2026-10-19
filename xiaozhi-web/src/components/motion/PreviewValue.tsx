import { useRef, type ReactNode } from 'react';
import gsap from 'gsap';
import { useGSAP } from '@gsap/react';

/** A small cue when an edited setting changes the local preview, never a save receipt. */
export default function PreviewValue({ children, value }: { children: ReactNode; value: unknown }) {
  const root = useRef<HTMLSpanElement>(null);
  const initialized = useRef(false);
  useGSAP(() => {
    if (!initialized.current) { initialized.current = true; return; }
    const media = gsap.matchMedia();
    media.add('(min-width: 761px) and (prefers-reduced-motion: no-preference)', () => {
      gsap.fromTo(root.current, { backgroundColor: '#eee9fb' }, { backgroundColor: 'transparent', duration: .9, clearProps: 'backgroundColor' });
    });
    return () => media.revert();
  }, { scope: root, dependencies: [value], revertOnUpdate: true });
  return <span className="preview-value" ref={root}>{children}</span>;
}
