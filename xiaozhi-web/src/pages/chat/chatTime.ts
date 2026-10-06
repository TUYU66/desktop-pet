// LocalDateTime from Java is Beijing wall time; explicit offsets remain instants.
export function chatTime(value: string): number {
  const normalized = value.trim().replace(' ', 'T');
  if (/^\d{4}-\d{2}-\d{2}$/.test(normalized)) return Date.parse(normalized + 'T00:00:00+08:00');
  return Date.parse(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(normalized) ? normalized : normalized + '+08:00');
}

const dayFormatter = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
});
const labelFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric',
});
const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
});

export function chatDate(value: string): string {
  const time = chatTime(value);
  return Number.isFinite(time) ? dayFormatter.format(time) : '';
}

export function chatDateLabel(value: string): string {
  const time = chatTime(value);
  return Number.isFinite(time) ? labelFormatter.format(time) : '日期未知';
}

export function chatTimeLabel(value: string): string {
  const time = chatTime(value);
  return Number.isFinite(time) ? timeFormatter.format(time) : '';
}
