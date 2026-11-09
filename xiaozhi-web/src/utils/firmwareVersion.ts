export function isNewerVersion(candidate: unknown, current: unknown): boolean {
  const valid = (value: unknown): value is string => typeof value === 'string' && /^\d+\.\d+\.\d+$/.test(value);
  if (!valid(candidate) || !valid(current)) return false;
  const a = candidate.split('.').map(BigInt), b = current.split('.').map(BigInt);
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return false;
}

function stm32Version(value: unknown): bigint | null {
  if (typeof value === 'number' && (!Number.isSafeInteger(value) || value <= 0)) return null;
  if (typeof value !== 'number' && (typeof value !== 'string' || !/^[1-9]\d{0,9}$/.test(value))) return null;
  const version = BigInt(value);
  return version > 0n && version <= 0xffffffffn ? version : null;
}

export function stm32FirmwareAction(candidate: unknown, state: {
  installedVersion?: unknown; phase?: string; active?: boolean; version?: unknown; recovery?: boolean;
} | null) {
  const target = stm32Version(candidate);
  if (target === null) return { allowed: false, label: '版本无效' };
  if (state?.recovery) return { allowed: true, label: '恢复底盘' };
  const live = stm32Version(state?.installedVersion);
  // 升级刚完成时，周期遥测可能还没刷新；只采信已确认完成的任务，不把目标当作已安装。
  const confirmed = state?.phase === 'completed' && !state.active ? stm32Version(state.version) : null;
  const installed = live === null ? confirmed : confirmed !== null && confirmed > live ? confirmed : live;
  if (installed === null || target > installed) return { allowed: true, label: '升级底盘' };
  return { allowed: false, label: target === installed ? '已是当前版本' : '旧版本' };
}
