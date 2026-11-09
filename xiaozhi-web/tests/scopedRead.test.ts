import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createScopedRead } from '../src/utils/scopedRead.ts';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('a StrictMode cleanup/remount fetches initial state without manual refresh', async () => {
  const old = deferred<string>(); const current = deferred<string>();
  const seen: string[] = []; const settled: string[] = [];
  let oldSignal: AbortSignal | undefined;
  const first = createScopedRead(signal => { oldSignal = signal; return old.promise; }, {
    succeeded: value => seen.push(value), failed: () => assert.fail('stale failure'), settled: () => settled.push('old'),
  });
  const oldRequest = first.run(); await Promise.resolve(); first.stop();
  const second = createScopedRead(() => current.promise, {
    succeeded: value => seen.push(value), failed: () => assert.fail('current failure'), settled: () => settled.push('current'),
  });
  const newRequest = second.run();
  assert.equal(oldSignal?.aborted, true);
  current.resolve('ready'); await newRequest;
  old.resolve('stale'); await oldRequest;
  assert.deepEqual(seen, ['ready']); assert.deepEqual(settled, ['current']);
});

test('refresh and polling share one request; failures release it for retry', async () => {
  const first = deferred<string>(); let calls = 0; let loading = false;
  const results: string[] = []; const errors: unknown[] = [];
  const reader = createScopedRead(() => ++calls === 1 ? first.promise : Promise.resolve('recovered'), {
    started: () => { loading = true; }, succeeded: value => results.push(value),
    failed: error => errors.push(error), settled: () => { loading = false; },
  });
  const request = reader.run(); assert.equal(reader.run(), request); assert.equal(reader.run(), request);
  await Promise.resolve(); assert.equal(calls, 1); assert.equal(loading, true);
  first.reject(new Error('offline')); await request;
  assert.equal(loading, false); assert.equal(errors.length, 1);
  await reader.run(); assert.equal(calls, 2); assert.deepEqual(results, ['recovered']);
});

test('unmount before dispatch and a late rejection cannot publish state or errors', async () => {
  let calls = 0; const read = createScopedRead(async () => { calls++; return 'ignored'; }, {
    succeeded: () => assert.fail('unmounted state'), failed: () => assert.fail('unmounted error'),
    settled: () => assert.fail('unmounted loading'),
  });
  const pending = read.run(); read.stop(); await pending; await read.run(); assert.equal(calls, 0);
  const late = deferred<string>();
  const reader = createScopedRead(() => late.promise, {
    succeeded: () => assert.fail('late state'), failed: () => assert.fail('late error'),
  });
  const request = reader.run(); await Promise.resolve(); reader.stop(); late.reject(new Error('late'));
  await request;
});
