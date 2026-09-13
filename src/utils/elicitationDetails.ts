export interface ElicitationDetail {
  status?: string;
  body?: string;
  elicitation_ids?: string[];
  elicitation_resumed?: boolean;
}

function inputIds(detail: ElicitationDetail): string[] {
  if (detail.elicitation_ids) return detail.elicitation_ids;
  try {
    const ids = JSON.parse(detail.body ?? '').elicitation_ids;
    return Array.isArray(ids) ? ids.filter((id) => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

/** Apply the latest recorded continuation outcome to its original tool detail. */
export function reconcileElicitationDetail<T extends ElicitationDetail>(
  detail: T,
  outcomes: ElicitationDetail[],
): T {
  if (detail.elicitation_resumed) return detail;
  const ids = inputIds(detail);
  if (!ids.length) return detail;
  const latest = outcomes.findLast(
    (outcome) =>
      outcome.elicitation_resumed &&
      inputIds(outcome).some((id) => ids.includes(id)),
  );
  return latest
    ? { ...detail, status: latest.status, body: latest.body }
    : { ...detail, status: 'awaiting' };
}
