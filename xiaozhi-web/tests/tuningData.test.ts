import { test } from 'node:test';
import assert from 'node:assert/strict';
import { completePoint, gyroValid, mergePoints, paramsValid, samplesConnect } from '../src/pages/dashboard/tuningData.ts';
import type { TuningState } from '../src/pages/dashboard/tuningData.ts';

const point = (id: number, tick: number, revision = 1, enabled = 1) =>
  [id, tick, revision, enabled, 420, 0, 0, 1, 1, 100, 50, 0, 1450, 1480, 100, 50, 0, 0, 0, 0];

test('telemetry never connects across dropped samples, revisions, stop/start or clock wrap gaps', () => {
  assert.equal(samplesConnect(point(1, 100), point(2, 200)), true);
  assert.equal(samplesConnect(point(1, 100), point(3, 200)), false);
  assert.equal(samplesConnect(point(1, 100), point(2, 200, 2)), false);
  assert.equal(samplesConnect(point(1, 100), point(2, 200, 1, 0)), false);
  assert.equal(samplesConnect(point(1, 100), point(2, 1000)), false);
  const moving = point(2, 200); moving[17] = 1;
  assert.equal(samplesConnect(point(1, 100), moving), false);
  assert.equal(samplesConnect(point(1, 0x7ffffff0), point(2, 20)), true);
});

test('history rejects duplicate points, caps retained samples and clears after device restart', () => {
  const previous = Array.from({ length: 300 }, (_, i) => point(i + 1, i * 100));
  const state = { reset: false, points: [point(300, 29900), point(301, 30000)] } as TuningState;
  const merged = mergePoints(previous, state);
  assert.equal(merged.length, 300);
  assert.equal(merged[0][0], 2);
  assert.equal(merged.at(-1)![0], 301);
  assert.deepEqual(mergePoints(previous, { ...state, reset: true, points: [point(1, 100)] }), [point(1, 100)]);
});

test('gain bounds and wire precision reject incomplete, nonfinite or unsafe drafts', () => {
  const p = { midAngle: 2.1, balanceKp: 9600, balanceKd: 50, velocityKp: 6200, velocityKi: 15, turnKd: 6 };
  assert.equal(paramsValid(p), true);
  assert.equal(paramsValid({ ...p, velocityKi: NaN }), false);
  assert.equal(paramsValid({ ...p, midAngle: 10.1 }), false);
  assert.equal(paramsValid({ ...p, balanceKp: 0 }), false);
  assert.equal(paramsValid({ ...p, balanceKd: 1.001 }), false);
  assert.equal(paramsValid({ ...p, turnKd: undefined }), false);
});

test('gyro state requires bounded actual bias and preserves raw versus corrected samples', () => {
  const g = { supported: true, valid: true, calibrated: true, revision: 1, status: 2, error: 0,
    progress: 100, pitchBias: 29, yawBias: -6, rawPitch: 30, rawYaw: -7, pitchCorrected: 1, yawCorrected: -1 };
  assert.equal(gyroValid(g), true);
  assert.equal(gyroValid({ ...g, pitchBias: 100 }), false);
  assert.equal(gyroValid({ ...g, pitchCorrected: NaN }), false);
  assert.equal(gyroValid({ ...g, status: 9 }), false);
  const legacy = point(1, 100).slice(0, 18); legacy[5] = 29; legacy[6] = -6;
  assert.deepEqual(completePoint(legacy).slice(18), [2900, -600]);
  const corrected = [...legacy, 100, -100];
  assert.deepEqual(completePoint(corrected), corrected);
});
