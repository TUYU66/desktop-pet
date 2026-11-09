export interface ScopedRead {
  run(): Promise<void>;
  stop(): void;
}

/** One read per lifecycle; an old lifecycle cannot settle or publish into a new one. */
export function createScopedRead<T>(read: (signal: AbortSignal) => Promise<T>, callbacks: {
  started?: () => void;
  succeeded: (value: T) => void;
  failed: (error: unknown) => void;
  settled?: () => void;
}): ScopedRead {
  let stopped = false;
  let pending: Promise<void> | undefined;
  let controller: AbortController | undefined;
  return {
    run() {
      if (stopped) return Promise.resolve();
      if (pending) return pending;
      controller = new AbortController();
      const signal = controller.signal;
      callbacks.started?.();
      pending = Promise.resolve().then(() => {
        if (stopped) return;
        return read(signal);
      }).then(value => {
        if (!stopped) callbacks.succeeded(value as T);
      }, error => {
        if (!stopped) callbacks.failed(error);
      }).finally(() => {
        pending = undefined;
        if (!stopped) callbacks.settled?.();
      });
      return pending;
    },
    stop() { stopped = true; controller?.abort(); },
  };
}
