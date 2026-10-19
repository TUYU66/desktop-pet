type Environment = {
  hidden: () => boolean;
  later: (callback: () => void, delay: number) => unknown;
  cancel: (timer: unknown) => void;
  onVisibility: (callback: () => void) => () => void;
};

type Options = {
  interval: number | (() => number);
  maxInterval?: number;
  leading?: boolean;
  enabled?: () => boolean;
  environment?: Environment;
};

/** Read-only polling: one request at a time, hidden-page pause and bounded backoff. */
export function startPolling(task: (signal: AbortSignal) => Promise<boolean | void>, options: Options) {
  const environment: Environment = options.environment ?? {
    hidden: () => document.hidden,
    later: (callback, delay) => window.setTimeout(callback, delay),
    cancel: timer => window.clearTimeout(timer as number),
    onVisibility: callback => {
      document.addEventListener('visibilitychange', callback);
      return () => document.removeEventListener('visibilitychange', callback);
    },
  };
  let stopped = false;
  let running = false;
  let requested = false;
  let failures = 0;
  let timer: unknown;
  let controller: AbortController | undefined;
  const clear = () => { if (timer !== undefined) environment.cancel(timer); timer = undefined; };
  const interval = () => typeof options.interval === 'function' ? options.interval() : options.interval;
  const schedule = () => {
    if (stopped || environment.hidden() || options.enabled?.() === false) return;
    const base = interval();
    timer = environment.later(() => { timer = undefined; void run(); },
      Math.min(base * 2 ** Math.min(failures, 6), options.maxInterval ?? Math.max(base, 60000)));
  };
  const run = async () => {
    clear();
    if (stopped || environment.hidden() || options.enabled?.() === false) return;
    if (running) { requested = true; return; }
    running = true;
    controller = new AbortController();
    try {
      const result = await task(controller.signal);
      if (!controller.signal.aborted) failures = result === false ? failures + 1 : 0;
    } catch {
      if (!controller.signal.aborted) failures += 1;
    } finally {
      running = false;
      if (!stopped && !environment.hidden()) {
        if (requested) { requested = false; void run(); }
        else schedule();
      }
    }
  };
  const removeVisibility = environment.onVisibility(() => {
    clear();
    if (environment.hidden()) controller?.abort();
    else { failures = 0; void run(); }
  });
  if (options.leading === false) schedule();
  else void run();
  return {
    refresh: () => { failures = 0; void run(); },
    resume: () => { if (!running && timer === undefined) { failures = 0; schedule(); } },
    stop: () => { stopped = true; clear(); controller?.abort(); removeVisibility(); },
  };
}
