export function temporalStatusLabel(status: string | null): string {
  if (!status) return 'Not run';
  if (status === 'success') return 'Completed';
  if (status === 'failure') return 'Failed';
  return status
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function temporalStatusColor(
  status: string | null,
): 'success' | 'info' | 'warning' | 'error' | 'default' {
  switch (status) {
    case 'success':
    case 'completed':
      return 'success';
    case 'running':
    case 'scheduled':
    case 'waiting':
      return 'info';
    case 'canceled':
    case 'cancel_requested':
    case 'paused':
      return 'warning';
    case 'failure':
    case 'failed':
    case 'timed_out':
    case 'terminated':
      return 'error';
    default:
      return 'default';
  }
}
