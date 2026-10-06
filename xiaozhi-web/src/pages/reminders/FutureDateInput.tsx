import { DatePicker } from 'antd';
import { useMemo } from 'react';
import dayjs from 'dayjs';

/** 表单仍存北京时间字符串；控件不依赖浏览器所在时区。 */
export default function FutureDateInput({ value, onChange, now, id }: { value?: string; onChange?: (value?: string) => void; now: number; id?: string }) {
  const pickerValue = useMemo(() => value ? dayjs(value) : null, [value]);
  const wallTime = (ms: number) => dayjs(new Date(ms + 8 * 3600000).toISOString().slice(0, 16));
  const min = wallTime((Math.floor(now / 60000) + 1) * 60000);
  const max = wallTime(now + 5 * 366 * 86400000);
  const range = (count: number) => Array.from({ length: count }, (_, i) => i);
  return <DatePicker id={id} value={pickerValue} onChange={date => onChange?.(date?.format('YYYY-MM-DDTHH:mm'))}
    style={{ width: '100%' }} format="YYYY-MM-DD HH:mm" inputReadOnly
    showTime={{ format: 'HH:mm' }} placeholder="选择未来的日期和时间"
    disabledDate={date => date.isBefore(min, 'day') || date.isAfter(max, 'day')}
    disabledTime={date => date?.isSame(min, 'day') ? {
      disabledHours: () => range(24).filter(hour => hour < min.hour()),
      disabledMinutes: hour => hour === min.hour() ? range(60).filter(minute => minute < min.minute()) : [],
    } : {}} />;
}
