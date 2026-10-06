export const recurrenceOptions = [{ label: '仅这一次', value: 'once' }, { label: '每天', value: 'daily' }, { label: '每周指定星期', value: 'weekly' }, { label: '每周多天', value: 'selected' }, { label: '周一至周五', value: 'weekdays' }];
export type ScheduleTime = { date?: string; time?: string; weekday?: number; selectedDays?: number[]; recurrence?: string };
export const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
/** 将北京时间周期规则转换为下一次触发时间，与浏览器时区无关。 */
export function nextTrigger(values: ScheduleTime, now: number): number {
  if (!values.recurrence || values.recurrence === 'once') return new Date(`${values.date}+08:00`).getTime();
  if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(values.time || '')) return NaN;
  if (values.recurrence === 'weekly' && (!Number.isInteger(values.weekday) || values.weekday! < 0 || values.weekday! > 6)) return NaN;
  if (values.recurrence === 'selected' && (!values.selectedDays?.length || values.selectedDays.some(day => !Number.isInteger(day) || day < 0 || day > 6))) return NaN;
  const today = new Date(now + 8 * 3600000).toISOString().slice(0, 10);
  const start = new Date(`${today}T${values.time}:00+08:00`).getTime();
  for (let i = 0; i <= 7; i++) {
    const at = start + i * 86400000;
    const day = new Date(at + 8 * 3600000).getUTCDay();
    if (at > now && (values.recurrence === 'selected' && values.selectedDays!.includes(day) || values.recurrence === 'daily' || values.recurrence === 'weekly' && day === values.weekday || values.recurrence === 'weekdays' && day >= 1 && day <= 5)) return at;
  }
  return NaN;
}

export function recurrenceCode(values: ScheduleTime): string {
  return values.recurrence === 'selected' ? 'weekly:' + [...new Set(values.selectedDays || [])].map(d => d === 0 ? 7 : d).sort().join(',') : values.recurrence || 'once';
}
export function recurrenceLabel(rule: string, anchor: number): string {
  if (rule.startsWith('weekly:')) return '每周' + rule.slice(7).split(',').map(d => ['','一','二','三','四','五','六','日'][Number(d)]).join('、');
  return rule === 'daily' ? '每天' : rule === 'weekdays' ? '周一至周五' : '每周' + weekdays[new Date(anchor + 28800000).getUTCDay()];
}
