import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isNewerVersion, stm32FirmwareAction } from '../src/utils/firmwareVersion.ts';

test('only newer embedded firmware versions enable a manual upgrade', () => {
  assert.equal(isNewerVersion('2.2.10', '2.2.9'), true);
  assert.equal(isNewerVersion('2.2.9', '2.2.10'), false);
  assert.equal(isNewerVersion('2.2.10', '2.2.10'), false);
  for (const invalid of [undefined, null, '1.2', '2.2.10-test', 3]) {
    assert.equal(isNewerVersion('2.2.10', invalid), false);
    assert.equal(isNewerVersion(invalid, '2.2.9'), false);
  }
});

test('STM32 only offers newer releases and immediately disables a confirmed completed release', () => {
  const state = { installedVersion: 2026110902 };
  assert.deepEqual(stm32FirmwareAction('2026110902', state), { allowed: false, label: '已是当前版本' });
  assert.deepEqual(stm32FirmwareAction('2026110901', state), { allowed: false, label: '旧版本' });
  assert.equal(stm32FirmwareAction('2026110903', state).allowed, true);
  assert.equal(stm32FirmwareAction('2026110902', { installedVersion: 0 }).allowed, true);
  const completed = { installedVersion: 2026110901, phase: 'completed', active: false, version: '2026110902' };
  assert.equal(stm32FirmwareAction('2026110902', completed).allowed, false);
  assert.equal(stm32FirmwareAction('2026110902', { ...completed, phase: 'failed' }).allowed, true);
  assert.equal(stm32FirmwareAction('2026110902', { ...completed, recovery: true }).allowed, true);
  for (const value of [undefined, '2.2.12', 'broken', -1, 4294967296]) {
    assert.equal(stm32FirmwareAction(value, state).allowed, false);
  }
});
