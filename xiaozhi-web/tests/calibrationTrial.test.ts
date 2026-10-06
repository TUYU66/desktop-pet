import { test } from 'node:test';
import assert from 'node:assert/strict';
import { holdingOutput, holdingValuesValid, observationReady } from '../src/pages/dashboard/calibrationTrial.ts';
import type { HoldingTrial } from '../src/pages/dashboard/calibrationTrial.ts';

const trial: HoldingTrial = {
  startedAt: 100, startupConfirmedAt: null, targetConfirmedAt: null, startup: 500, target: 320,
};

test('assist waits for device confirmation and lowering output never renews the eight second deadline', () => {
  assert.equal(holdingOutput(trial, 2000), 500);
  const confirmed = { ...trial, startupConfirmedAt: 2000 };
  assert.equal(holdingOutput(confirmed, 3199), 500);
  assert.equal(holdingOutput(confirmed, 3200), 320);
  assert.equal(holdingOutput(confirmed, 8099), 320);
  assert.equal(holdingOutput(confirmed, 8100), null);
  assert.equal(holdingOutput({ ...confirmed, startupConfirmedAt: 8000 }, 8100), null);
});

test('a result needs observation after target confirmation, not merely startup or elapsed request time', () => {
  assert.equal(observationReady(trial, 7000), false);
  const observing = { ...trial, startupConfirmedAt: 200, targetConfirmedAt: 1800 };
  assert.equal(observationReady(observing, 3799), false);
  assert.equal(observationReady(observing, 3800), true);
});

test('invalid or reversed output ranges cannot begin a holding trial', () => {
  for (const [startup, target] of [[0, 100], [500, 0], [500, 501], [2601, 300], [500, 300.5], [NaN, 300]]) {
    assert.equal(holdingValuesValid(startup, target), false);
    assert.equal(holdingOutput({ ...trial, startup, target }, 200), null);
  }
  assert.equal(holdingValuesValid(500, 320), true);
  assert.equal(holdingValuesValid(500, 500), true);
});
