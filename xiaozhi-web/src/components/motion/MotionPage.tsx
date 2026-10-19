import { useRef, type ReactNode } from 'react';
import gsap from 'gsap';
import { useGSAP } from '@gsap/react';

gsap.registerPlugin(useGSAP);

// Reveal meaningful groups once per route mount, including asynchronously loaded
// panels. Polling text updates never replay an entrance or change scroll position.
const groups = '.page-heading, .music-heading, .companion-welcome, .live-device-panel, .recent-conversations, .memory-group, .memory-empty, .reminder-create, .reminder-plans, .reminder-queue, .reminder-pending, .reminder-history, .settings-card, .settings-preview, .avatar-settings, .music-player-details, .music-collection, .netease-search, .chat-date-toolbar, .composer';

export default function MotionPage({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add('(min-width: 761px) and (prefers-reduced-motion: no-preference)', () => {
      const scope = root.current!;
      scope.classList.add('motion-enhanced');
      const seen = new WeakSet<Element>();
      const revealed = new WeakSet<Element>();
      const entrance = (element: Element) => {
        if (revealed.has(element)) return;
        revealed.add(element);
        // Auto-alpha is deliberately avoided: content remains available if JS stops.
        gsap.fromTo(element, { opacity: .45, y: 14 }, {
          opacity: 1, y: 0, duration: .55, ease: 'power3.out', clearProps: 'opacity,transform', overwrite: 'auto',
        });
      };
      const context = gsap.context(() => {}, scope);
      // Observer callbacks execute after setup, so record their tweens for cleanup.
      context.add('reveal', entrance);
      const streamEnds = new WeakMap<Element, Element>();
      context.add('messages', () => {
        scope.querySelectorAll('.message-stream').forEach(stream => {
          const rows = Array.from(stream.querySelectorAll('.message-row'));
          const previous = streamEnds.get(stream);
          const last = rows.at(-1);
          if (!last) { streamEnds.delete(stream); return; }
          streamEnds.set(stream, last);
          // First page and older-history loads stay still; only appended rows move.
          const index = previous ? rows.indexOf(previous) : -1;
          if (index >= 0 && index < rows.length - 1) {
            gsap.fromTo(rows.slice(index + 1), { opacity: .3, x: 8 }, {
              opacity: 1, x: 0, stagger: .04, duration: .3, ease: 'power2.out', clearProps: 'opacity,transform',
            });
          }
        });
      });
      const scopedObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (entry.isIntersecting) { context.reveal(entry.target); scopedObserver.unobserve(entry.target); }
        });
      }, { threshold: .08 });
      const discover = () => {
        scope.querySelectorAll(groups).forEach(element => {
          if (seen.has(element)) return;
          seen.add(element);
          scopedObserver.observe(element);
        });
        context.messages();
      };
      discover();
      const mutations = new MutationObserver(records => {
        if (records.some(record => record.addedNodes.length)) discover();
      });
      mutations.observe(scope, { childList: true, subtree: true });
      // Native details remain keyboard accessible; animate their content only.
      context.add('unfold', (event: Event) => {
        const details = event.target;
        if (details instanceof HTMLDetailsElement && details.open) {
          const content = Array.from(details.children).filter(child => child.tagName !== 'SUMMARY');
          if (!content.length) return;
          gsap.fromTo(content,
            { opacity: .4, y: -5 }, { opacity: 1, y: 0, duration: .26, clearProps: 'opacity,transform' });
        }
      });
      scope.addEventListener('toggle', context.unfold, true);
      return () => {
        mutations.disconnect(); scopedObserver.disconnect();
        scope.removeEventListener('toggle', context.unfold, true);
        context.revert(); scope.classList.remove('motion-enhanced');
      };
    });
    return () => media.revert();
  }, { scope: root });
  return <div ref={root} className="page-motion">{children}</div>;
}
