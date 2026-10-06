export type TuningParams = {
  midAngle: number; balanceKp: number; balanceKd: number;
  velocityKp: number; velocityKi: number; turnKd: number;
};
export const tuningFields: { key: keyof TuningParams; label: string; min: number; max: number; step: number }[] = [
  { key: 'midAngle', label: '机械平衡角（°）', min: -10, max: 10, step: .1 },
  { key: 'balanceKp', label: '平衡 P', min: 1000, max: 20000, step: 100 },
  { key: 'balanceKd', label: '平衡 D', min: 0, max: 200, step: 1 },
  { key: 'velocityKp', label: '速度 P', min: 0, max: 12000, step: 100 },
  { key: 'velocityKi', label: '速度 I', min: 0, max: 100, step: 1 },
  { key: 'turnKd', label: '静止转向阻尼 D', min: 0, max: 60, step: 1 },
];

// cursor, sensor tick, revision, enabled, angle x100, raw pitch/yaw gyro,
// left/right encoder counts per control sample, balance/velocity/yaw PWM,
// physical left/right PWM, speed integral, filtered speed x100, assist mask, motion flags,
// corrected pitch/yaw gyro x100. Legacy rows append uncalibrated gyro values.
export type TuningPoint = number[];
export type GyroState = {
  supported: boolean; valid: boolean; calibrated: boolean;
  revision: number; status: number; error: number; progress: number;
  pitchBias: number; yawBias: number; rawPitch: number; rawYaw: number;
  pitchCorrected: number; yawCorrected: number;
};
export function gyroValid(value: unknown): value is GyroState {
  if (!value || typeof value !== 'object') return false;
  const g = value as GyroState;
  return [g.supported, g.valid, g.calibrated].every(v => typeof v === 'boolean') &&
    Number.isInteger(g.revision) && g.revision >= 0 && g.revision <= 2147483646 &&
    Number.isInteger(g.status) && g.status >= 0 && g.status <= 3 &&
    Number.isInteger(g.error) && g.error >= 0 && g.error <= 6 &&
    Number.isInteger(g.progress) && g.progress >= 0 && g.progress <= 100 &&
    [g.pitchBias, g.yawBias].every(v => Number.isFinite(v) && Math.abs(v) <= 82) &&
    [g.rawPitch, g.rawYaw, g.pitchCorrected, g.yawCorrected].every(v => Number.isFinite(v) && Math.abs(v) <= 33000);
}
export const completePoint = (p: TuningPoint): TuningPoint => p.length === 18 ? [...p, p[5] * 100, p[6] * 100] : p;
export type TuningState = {
  supported: boolean; valid: boolean; telemetryValid: boolean;
  epoch: number; tick: number; revision: number; writable: boolean; hasPrevious: boolean;
  params: TuningParams | null; saved: TuningParams | null;
  cursor: number; reset: boolean; gap: boolean; points: TuningPoint[];
  gyro?: GyroState;
};

export function paramsValid(params: unknown): params is TuningParams {
  if (!params || typeof params !== 'object') return false;
  return tuningFields.every(({ key, min, max }) => {
    const value = (params as TuningParams)[key];
    const scale = key === 'midAngle' ? 1000 : 100;
    return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max &&
      Math.abs(value * scale - Math.round(value * scale)) < 1e-6;
  });
}

export function stateValid(value: unknown): value is TuningState {
  if (!value || typeof value !== 'object') return false;
  const s = value as TuningState;
  return ['supported', 'valid', 'telemetryValid', 'writable', 'hasPrevious', 'reset', 'gap']
    .every(key => typeof (s as unknown as Record<string, unknown>)[key] === 'boolean') &&
    [s.epoch, s.tick, s.revision, s.cursor].every(n => Number.isInteger(n) && n >= 0 && n <= 2147483647) &&
    (!s.valid || paramsValid(s.params)) && (s.saved === null || paramsValid(s.saved)) &&
    (s.gyro === undefined || gyroValid(s.gyro)) &&
    Array.isArray(s.points) && s.points.length <= 30 && s.points.every(p =>
      Array.isArray(p) && (p.length === 18 || p.length === 20) && p.every(n => Number.isFinite(n) && Number.isInteger(n)) &&
      p[0] > 0 && p[1] >= 0 && p[1] <= 2147483647 && p[2] > 0 &&
      (p[3] === 0 || p[3] === 1) && p[16] >= 0 && p[16] <= 3 && p[17] >= 0 && p[17] <= 63);
}

export function mergePoints(previous: TuningPoint[], state: TuningState): TuningPoint[] {
  const merged = state.reset ? [] : previous.slice();
  for (const point of state.points) {
    const last = merged.at(-1);
    if (last && point[0] <= last[0]) continue;
    merged.push(completePoint(point));
  }
  return merged.slice(-300);
}

export function samplesConnect(a: TuningPoint, b: TuningPoint): boolean {
  const delta = (b[1] - a[1]) & 0x7fffffff;
  return b[0] === a[0] + 1 && a[2] === b[2] && a[3] === b[3] && a[17] === b[17] && delta > 0 && delta <= 250;
}
