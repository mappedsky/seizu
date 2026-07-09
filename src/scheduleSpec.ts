// Shared ScheduleSpec type + helpers for scheduled queries and scheduled
// chats. Mirrors the backend ScheduleSpec model (seizu_schema). Scheduled
// chats are limited to hourly granularity (no 'interval' type, no minute).

export interface ScheduleSpec {
  type: 'interval' | 'hourly' | 'daily' | 'monthly';
  // interval: run every N minutes.
  interval_minutes?: number | null;
  // hourly: run every N hours.
  interval_hours?: number | null;
  // daily: 0=Monday .. 6=Sunday.
  days_of_week?: number[];
  // daily: hour of day (UTC).
  hour?: number;
  // daily: minute of hour (UTC); scheduled chats always use 0.
  minute?: number;
  // monthly: days 1-31; months without a selected day run on their last day.
  days_of_month?: number[];
}

// weekday() order: 0=Monday .. 6=Sunday.
export const DAY_OF_WEEK_LABELS = [
  'Mon',
  'Tue',
  'Wed',
  'Thu',
  'Fri',
  'Sat',
  'Sun',
];

export function formatTimeOfDay(hour: number, minute: number): string {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

export function describeSchedule(schedule: ScheduleSpec): string {
  if (schedule.type === 'interval') {
    const minutes = schedule.interval_minutes ?? 1;
    return minutes === 1 ? 'Every minute' : `Every ${minutes} min`;
  }
  if (schedule.type === 'hourly') {
    const hours = schedule.interval_hours ?? 1;
    return hours === 1 ? 'Hourly' : `Every ${hours} h`;
  }
  if (schedule.type === 'daily') {
    const days = (schedule.days_of_week ?? [])
      .map((day) => DAY_OF_WEEK_LABELS[day])
      .join(', ');
    return `${days} at ${formatTimeOfDay(schedule.hour ?? 0, schedule.minute ?? 0)} UTC`;
  }
  return `Monthly on ${(schedule.days_of_month ?? []).join(', ')}`;
}
