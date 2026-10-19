type Kind = 'chat' | 'memory' | 'reminder' | 'music' | 'settings';

/** Decorative sketches only: never imply a device or request state. */
export default function PageDoodle({ kind }: { kind: Kind }) {
  return <svg className={`page-doodle page-doodle--${kind}`} aria-hidden="true" focusable="false" viewBox="0 0 92 52" fill="none">
    <g stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      {kind === 'chat' && <><path className="doodle-body" d="M17 10h47a9 9 0 0 1 9 9v14a9 9 0 0 1-9 9H38L25 49v-7h-8a9 9 0 0 1-9-9V19a9 9 0 0 1 9-9Z" /><g className="doodle-dots" fill="currentColor" stroke="none"><circle cx="28" cy="26" r="2.2" /><circle cx="42" cy="26" r="2.2" /><circle cx="56" cy="26" r="2.2" /></g></>}
      {kind === 'memory' && <><path className="doodle-paper" fill="#f7f5ff" d="m23 5 42 3-3 40-42-3Z" /><path className="doodle-body" fill="white" d="m15 10 40-3 3 37-40 3Z" /><path d="m25 21 20-2m-19 9 15-1m-14 9 20-2" /><path className="doodle-star" d="m72 13 2 6 6 2-6 2-2 6-2-6-6-2 6-2Z" /></>}
      {kind === 'reminder' && <><path className="doodle-bell" d="m20 11 8-6m34 0 8 6" /><circle className="doodle-body" cx="45" cy="28" r="18" fill="#fff9e9" /><path className="doodle-hand" d="M45 17v12l8 5" /><path d="m33 45-4 4m28-4 4 4" /><path className="doodle-star" d="M78 21v7m-3-4h6" /></>}
      {kind === 'music' && <><circle className="doodle-body" cx="37" cy="28" r="19" fill="#f3f0ff" /><circle cx="37" cy="28" r="6" /><path className="doodle-record" d="M25 24a13 13 0 0 1 12-9m0 26a13 13 0 0 0 12-9" /><g className="doodle-note"><path d="M67 32V10l14-3v21M67 17l14-3" /><ellipse cx="63" cy="33" rx="4" ry="3" /><ellipse cx="77" cy="29" rx="4" ry="3" /></g></>}
      {kind === 'settings' && <><path d="M16 15h61M16 28h61M16 41h61" /><g className="doodle-knob-a"><rect x="31" y="10" width="10" height="10" rx="3" fill="#e6e0f5" /><rect x="53" y="36" width="10" height="10" rx="3" fill="#fef7d6" /></g><rect className="doodle-knob-b" x="57" y="23" width="10" height="10" rx="3" fill="white" /></>}
    </g>
  </svg>;
}
