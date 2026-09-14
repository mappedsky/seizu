import type { ScheduleSpec } from 'src/scheduleSpec';
import type { WorkflowWatchScan } from 'src/hooks/useWorkflowsApi';

/**
 * How a workflow is triggered, in one phrase: watch scans first, then the
 * schedule, then manual dispatch. Shared so the list and the version history
 * describe the same trigger the same way.
 */
export function workflowTriggerLabel(item: {
  schedule: ScheduleSpec | null;
  watch_scans: WorkflowWatchScan[];
}): string {
  if (item.watch_scans.length)
    return `${item.watch_scans.length} watch scan(s)`;
  if (!item.schedule) return 'Manual only';
  if (item.schedule.type === 'interval')
    return `Every ${item.schedule.interval_minutes} min`;
  if (item.schedule.type === 'hourly')
    return `Every ${item.schedule.interval_hours} hour(s)`;
  return item.schedule.type === 'daily' ? 'Daily schedule' : 'Monthly schedule';
}

/** Stage and activity counts for a workflow definition. */
export function workflowPipelineLabel(item: {
  stages: { activities: unknown[] }[];
}): string {
  const activities = item.stages.reduce(
    (total, stage) => total + stage.activities.length,
    0,
  );
  return `${item.stages.length} stage(s), ${activities} activity(ies)`;
}
