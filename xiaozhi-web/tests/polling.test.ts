import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startPolling } from '../src/utils/polling.ts';

function clock() {
  let hidden = false;
  let listener: (() => void) | undefined;
  let pending: { callback: () => void; delay: number } | undefined;
  return {
    environment: {
      hidden: () => hidden,
      later: (callback: () => void, delay: number) => { pending = { callback, delay }; return pending; },
      cancel: () => { pending = undefined; },
      onVisibility: (callback: () => void) => { listener = callback; return () => { listener = undefined; }; },
    },
    get delay() { return pending?.delay; },
    tick() { const callback = pending?.callback; pending = undefined; callback?.(); },
    hide(value: boolean) { hidden = value; listener?.(); },
  };
}
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

test('terminal or unsupported states stop scheduling until explicitly enabled', async () => {
  const timer = clock();
  let enabled = true;
  let calls = 0;
  const poll = startPolling(async () => { calls++; enabled = false; },
    { interval: 1000, enabled: () => enabled, environment: timer.environment });
  await settle(); assert.equal(calls, 1); assert.equal(timer.delay, undefined);
  timer.hide(true); timer.hide(false); poll.refresh(); await settle(); assert.equal(calls, 1);
  enabled = true; poll.refresh(); await settle(); assert.equal(calls, 2);
  enabled = true; poll.resume(); assert.equal(timer.delay, 1000);
  poll.resume(); timer.tick(); await settle(); assert.equal(calls, 3);
  poll.stop();
});

test('polling backs off failures, recovers, and stops on cleanup', async () => {
  const timer = clock();
  let ok = false;
  let calls = 0;
  const poll = startPolling(async () => { calls++; return ok; }, { interval: 1000, maxInterval: 4000, environment: timer.environment });
  await settle(); assert.equal(timer.delay, 2000);
  timer.tick(); await settle(); assert.equal(timer.delay, 4000);
  timer.tick(); await settle(); assert.equal(timer.delay, 4000);
  ok = true; timer.tick(); await settle(); assert.equal(timer.delay, 1000);
  poll.stop(); assert.equal(timer.delay, undefined);
  poll.refresh(); timer.hide(false); await settle(); assert.equal(calls, 4);
});

test('visibility aborts a pending read; resume and refresh never overlap reads', async () => {
  const timer = clock();
  let finish: (() => void) | undefined;
  let signal: AbortSignal | undefined;
  let calls = 0;
  const poll = startPolling(async incoming => {
    calls++; signal = incoming;
    await new Promise<void>(resolve => { finish = resolve; });
  }, { interval: 1000, environment: timer.environment });
  poll.refresh(); poll.refresh(); assert.equal(calls, 1);
  timer.hide(true); assert.equal(signal?.aborted, true); assert.equal(timer.delay, undefined);
  timer.hide(false); assert.equal(calls, 1);
  finish?.(); await settle(); assert.equal(calls, 2);
  timer.hide(true); finish?.(); await settle(); assert.equal(timer.delay, undefined);
  timer.hide(false); assert.equal(calls, 3);
  poll.stop(); assert.equal(signal?.aborted, true);
  finish?.(); await settle(); assert.equal(timer.delay, undefined);
});
