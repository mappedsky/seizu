import type { WorkflowRunActivity } from 'src/hooks/useWorkflowsApi';

// A configured workflow names every activity it schedules for a stage
// `stage:{n}:activity:{m}:{output}:{kind}`; a code-defined workflow's child
// carries the same tail on its workflow id. Anything else in a run's history
// is the engine's own bookkeeping around those stages.
const STAGED_ID_RE = /(?:^|:)stage:(\d+):activity:(\d+):([^:]+):([^:]+)$/;

// Parts a code-defined workflow activity runs around its child workflow.
const HELPER_KINDS = new Set(['build_input', 'normalize_output']);

const INTERNAL_LABELS: Record<string, string> = {
  load_configured_workflow: 'Load definition',
  record_configured_workflow_result: 'Record result',
  trigger_configured_workflows: 'Start downstream workflows',
  seizu_configured_workflow_execution: 'Workflow lock',
  seizu_configured_workflow_waiting_slot: 'Waiting slot',
};

export interface RunStep {
  key: string;
  activityPosition: number;
  output: string;
  kind: string;
  status: string;
  attempts: number;
  startedAt: string | null;
  closedAt: string | null;
  /** Every Temporal record belonging to this configured activity, in order. */
  parts: WorkflowRunActivity[];
}

export interface RunStage {
  position: number;
  steps: RunStep[];
}

export interface RunStructure {
  stages: RunStage[];
  internal: WorkflowRunActivity[];
}

export function internalActivityLabel(activity: WorkflowRunActivity): string {
  return INTERNAL_LABELS[activity.activity_type] ?? activity.activity_type;
}

// Most telling status first: a failure anywhere in a step is the step's
// status, then anything still in flight, then cancellation.
const STATUS_RANK: Record<string, number> = {
  failed: 0,
  failure: 0,
  timed_out: 0,
  terminated: 0,
  running: 1,
  scheduled: 1,
  waiting: 1,
  cancel_requested: 1,
  paused: 1,
  canceled: 2,
};

function stepStatus(parts: WorkflowRunActivity[]): string {
  return parts.reduce(
    (worst, part) =>
      (STATUS_RANK[part.status] ?? 3) < (STATUS_RANK[worst] ?? 3)
        ? part.status
        : worst,
    parts[0].status,
  );
}

function earliest(values: (string | null)[]): string | null {
  const present = values.filter((value): value is string => Boolean(value));
  return present.length ? present.reduce((a, b) => (a < b ? a : b)) : null;
}

export function groupRunActivities(
  activities: WorkflowRunActivity[],
): RunStructure {
  const stages = new Map<number, Map<number, RunStep>>();
  const internal: WorkflowRunActivity[] = [];
  for (const activity of activities) {
    const match = STAGED_ID_RE.exec(activity.activity_id);
    if (!match) {
      internal.push(activity);
      continue;
    }
    const [, stage, position, output, kind] = match;
    const stagePosition = Number(stage);
    const activityPosition = Number(position);
    const steps = stages.get(stagePosition) ?? new Map<number, RunStep>();
    stages.set(stagePosition, steps);
    const step = steps.get(activityPosition) ?? {
      key: `${stagePosition}:${activityPosition}`,
      activityPosition,
      output,
      kind,
      status: activity.status,
      attempts: 0,
      startedAt: null,
      closedAt: null,
      parts: [],
    };
    // The step is named for what it does, not for a helper running beside it.
    if (!HELPER_KINDS.has(kind)) step.kind = kind;
    step.parts.push(activity);
    steps.set(activityPosition, step);
  }
  return {
    stages: [...stages.entries()]
      .sort(([a], [b]) => a - b)
      .map(([position, steps]) => ({
        position,
        steps: [...steps.values()]
          .sort((a, b) => a.activityPosition - b.activityPosition)
          .map((step) => {
            const closed = step.parts.map((part) => part.closed_at);
            return {
              ...step,
              status: stepStatus(step.parts),
              attempts: Math.max(...step.parts.map((part) => part.attempts)),
              startedAt: earliest(
                step.parts.map((part) => part.started_at ?? part.scheduled_at),
              ),
              closedAt: closed.every(Boolean)
                ? closed.reduce((a, b) => ((a ?? '') > (b ?? '') ? a : b))
                : null,
            };
          }),
      })),
    internal,
  };
}

/** How a configured workflow run was started, read off its workflow id. */
export function runTrigger(workflowId: string): string | null {
  if (!workflowId.startsWith('seizu-workflow:')) return null;
  if (workflowId.includes(':manual:')) return 'Manual';
  if (workflowId.includes(':trigger:')) return 'Upstream workflow';
  if (workflowId.includes(':run:')) return 'Watch scan';
  if (/-\d{4}-\d{2}-\d{2}T[\d:.]+Z$/.test(workflowId)) return 'Schedule';
  return null;
}

export function formatDuration(
  start: string | null,
  end: string | null,
  now: number = Date.now(),
): string | null {
  if (!start) return null;
  const ms = (end ? Date.parse(end) : now) - Date.parse(start);
  if (!Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.max(1, Math.round(ms))} ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

export const ACTIVE_RUN_STATUSES = new Set([
  'running',
  'scheduled',
  'waiting',
  'cancel_requested',
]);
