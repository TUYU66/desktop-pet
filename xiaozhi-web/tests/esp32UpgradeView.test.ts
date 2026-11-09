import test from 'node:test';
import assert from 'node:assert/strict';
import { esp32UpgradeView } from '../src/utils/esp32UpgradeView.ts';

test('unavailable progress is indeterminate, not a fabricated percentage', () => {
  for (const progress of [undefined, null, '50', -1, 101, NaN, 3.5]) {
    assert.equal(esp32UpgradeView({ phase: 'requested', active: true, progress }).percent, null);
  }
  assert.equal(esp32UpgradeView({ phase: 'queued', active: true, progress: 0 }).percent, 0);
});

test('only a confirmed reboot completes the progress bar', () => {
  const writing = esp32UpgradeView({ phase: 'downloading', active: true, progress: 62 });
  assert.equal(writing.percent, 62); assert.equal(writing.status, 'active');
  assert.equal(esp32UpgradeView({ phase: 'rebooting', active: true, progress: 100 }).percent, 99);
  assert.equal(esp32UpgradeView({ phase: 'unconfirmed', progress: 99 }).status, 'normal');
  const complete = esp32UpgradeView({ phase: 'completed', progress: 99, version: '2.2.12' });
  assert.equal(complete.percent, 100); assert.equal(complete.status, 'success');
  assert.equal(esp32UpgradeView({ phase: 'failed', progress: 50 }).status, 'exception');
});
