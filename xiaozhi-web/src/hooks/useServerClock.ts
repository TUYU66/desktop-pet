import { useEffect, useRef, useState } from 'react';

/** Display countdowns against the latest server clock, never mutate state on expiry. */
export default function useServerClock(serverNow?: number) {
  const clock = useRef({ server: serverNow ?? Date.now(), received: performance.now() });
  const [now, setNow] = useState(clock.current.server);
  useEffect(() => {
    if (serverNow !== undefined) clock.current = { server: serverNow, received: performance.now() };
    const tick = () => setNow(clock.current.server + performance.now() - clock.current.received);
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [serverNow]);
  return now;
}
