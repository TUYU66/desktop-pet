import { Form, Select, TimePicker } from 'antd';
import { useMemo } from 'react';
import dayjs from 'dayjs';
import FutureDateInput from './FutureDateInput';
import { weekdays, recurrenceOptions } from './scheduleTime';

function TimeInput({ value, onChange, id }: { value?: string; onChange?: (value?: string) => void; id?: string }) {
  // The reminder clock rerenders every second. A fresh Dayjs value on each
  // render resets the picker's in-progress selection to its controlled value.
  const pickerValue = useMemo(() => value ? dayjs(`2000-01-01T${value}`) : null, [value]);
  const selectTime = (v: dayjs.Dayjs | dayjs.Dayjs[] | null) => {
    const next = (Array.isArray(v) ? v[0] : v)?.format('HH:mm');
    if (next !== value) onChange?.(next);
  };
  // Panel clicks must reach the form before blur/confirmation, so clock ticks
  // and status polling cannot overwrite the newly selected hour or minute.
  return <TimePicker id={id} value={pickerValue} onCalendarChange={selectTime} onChange={selectTime} needConfirm={false} format="HH:mm" inputReadOnly showNow={false} placeholder="选择时间" style={{ width: '100%' }} />;
}
export default function ScheduleFields({ recurrence = 'once', now, plan = false }: { recurrence?: string; now: number; plan?: boolean }) {
  return <>
    <Form.Item name="recurrence" label="重复"><Select options={plan ? recurrenceOptions.filter(o => o.value !== 'once') : recurrenceOptions} /></Form.Item>
    {recurrence === 'once' ? <Form.Item name="date" label="日期和时间（北京时间）" rules={[{ required: true, message: '请选择未来的日期和时间' }]}><FutureDateInput now={now} /></Form.Item> : <>
      {recurrence === 'selected' && <Form.Item name="selectedDays" label="每周哪几天" rules={[{ required: true, type: 'array', min: 1, message: '至少选择一天' }]}><Select mode="multiple" placeholder="选择星期" options={[1,2,3,4,5,6,0].map(value => ({ value, label: weekdays[value] }))} /></Form.Item>}
      {recurrence === 'weekly' && <Form.Item name="weekday" label="每周星期几" rules={[{ required: true, message: '请选择星期几' }]}><Select placeholder="选择星期几" options={[1, 2, 3, 4, 5, 6, 0].map(value => ({ value, label: weekdays[value] }))} /></Form.Item>}
      <Form.Item name="time" label="提醒时间（北京时间）" rules={[{ required: true, message: '请选择提醒时间' }]}><TimeInput /></Form.Item>
      {recurrence === 'weekdays' && <p className="reminder-meta">按星期执行，不随节假日调休。</p>}
    </>}
  </>;
}
