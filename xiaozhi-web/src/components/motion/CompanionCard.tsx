import { useEffect, useRef, useState, type ReactNode } from 'react';
import gsap from 'gsap';
import { useGSAP } from '@gsap/react';
import ClickSpark from '../react-bits/ClickSpark';

const query = '(min-width: 761px) and (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)';

export default function CompanionCard({ children }: { children: ReactNode }) {
  const root = useRef<HTMLElement>(null);
  const [interactive, setInteractive] = useState(false);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setInteractive(media.matches);
    update(); media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add(query, () => {
      const card = root.current!;
      const face = card.querySelector('.companion-face');
      const eyes = card.querySelectorAll('.companion-face i, .companion-face > span');
      if (!face) return;
      const rotate = gsap.quickTo(face, 'rotation', { duration: .65, ease: 'power3.out' });
      const lift = gsap.quickTo(face, 'y', { duration: .65, ease: 'power3.out' });
      const lookX = gsap.quickTo(eyes, 'x', { duration: .35, ease: 'power2.out' });
      const lookY = gsap.quickTo(eyes, 'y', { duration: .35, ease: 'power2.out' });
      const move = (event: PointerEvent) => {
        const box = card.getBoundingClientRect();
        const x = (event.clientX - box.left) / box.width - .5;
        const y = (event.clientY - box.top) / box.height - .5;
        rotate(-5 + x * 12); lift(y * 6 - 3); lookX(x * 10); lookY(y * 8);
      };
      const reset = () => { rotate(-5); lift(0); lookX(0); lookY(0); };
      card.addEventListener('pointermove', move);
      card.addEventListener('pointerleave', reset);
      return () => { card.removeEventListener('pointermove', move); card.removeEventListener('pointerleave', reset); };
    });
    return () => media.revert();
  }, { scope: root });
  return <section ref={root} className="companion-card" aria-label="桌面伙伴">
    <ClickSpark className="companion-welcome" disabled={!interactive} sparkColor={['#8c7cc3', '#b5a26c']} sparkCount={6}
      sparkSize={7} sparkRadius={22} sparkWidth={1.4} duration={420} randomness={.15}>
      {children}
    </ClickSpark>
  </section>;
}
