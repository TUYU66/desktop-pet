export function esp32UpgradeView(state: { phase?: string; active?: boolean; progress?: unknown; version?: string } | null) {
  const phase = state?.phase || 'idle';
  const progress = state?.progress;
  const percent = phase === 'completed' ? 100 : typeof progress === 'number' && Number.isInteger(progress) && progress >= 0 && progress <= 100 ? Math.min(99, progress) : null;
  const labels: Record<string, string> = {
    requested: '已发送升级请求，等待设备开始更新', queued: '正在准备升级',
    downloading: '正在下载并写入固件', rebooting: '固件已写入，等待重启并确认版本',
    unconfirmed: '升级结果待确认，请刷新状态，不要重复发起',
    completed: `ESP32 已启动新版本 ${state?.version || ''}`,
    failed: 'ESP32 升级未完成，请检查设备状态后重试',
  };
  const status = phase === 'completed' ? 'success' : phase === 'failed' ? 'exception' : state?.active ? 'active' : 'normal';
  return { percent, status: status as 'success' | 'exception' | 'active' | 'normal', label: labels[phase] || '升级状态未知' };
}
