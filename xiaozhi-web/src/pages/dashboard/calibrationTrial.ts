export const TRIAL_LIMIT_MS = 8000;
export const START_ASSIST_MS = 1200;
export const MIN_OBSERVATION_MS = 2000;

export type HoldingTrial = {
  startedAt: number;
  startupConfirmedAt: number | null;
  targetConfirmedAt: number | null;
  startup: number;
  target: number;
};

export function holdingValuesValid(startup: number, target: number): boolean {
  return Number.isInteger(startup) && Number.isInteger(target) &&
    startup > 0 && startup <= 2600 && target > 0 && target <= startup;
}

// The deadline belongs to the entire trial. Changing output never extends it.
export function holdingOutput(trial: HoldingTrial, now: number): number | null {
  if (!holdingValuesValid(trial.startup, trial.target) || now - trial.startedAt >= TRIAL_LIMIT_MS) return null;
  return trial.startupConfirmedAt === null || now - trial.startupConfirmedAt < START_ASSIST_MS
    ? trial.startup : trial.target;
}

export function observationReady(trial: HoldingTrial, now: number): boolean {
  return trial.targetConfirmedAt !== null && now - trial.targetConfirmedAt >= MIN_OBSERVATION_MS;
}
