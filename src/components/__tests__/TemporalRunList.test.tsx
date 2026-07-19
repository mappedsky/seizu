import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import TemporalRunList from 'src/components/TemporalRunList';
import {
  WorkflowRunDetail,
  WorkflowRunSummary,
} from 'src/hooks/useWorkflowsApi';

afterEach(cleanup);

const run: WorkflowRunSummary = {
  workflow_id: 'seizu-workflow:workflow-1:scheduled',
  run_id: 'run-1',
  workflow_name: 'seizu_configured_workflow',
  status: 'completed',
  start_time: '2026-07-17T01:00:00Z',
  close_time: '2026-07-17T01:02:00Z',
  history_length: 14,
};

const detail: WorkflowRunDetail = {
  ...run,
  failure: null,
  activities: [
    {
      activity_id: 'stage:1:activity:1:findings:query',
      activity_type: 'execute_configured_query',
      status: 'completed',
      attempts: 1,
      maximum_attempts: 3,
      scheduled_at: '2026-07-17T01:00:00Z',
      started_at: '2026-07-17T01:00:01Z',
      closed_at: '2026-07-17T01:00:02Z',
      retry_state: null,
      failure: null,
      last_attempt_failure: null,
      input_preview: '{"cypher":"RETURN 1"}',
      result_preview: '{"output_id":"findings","value":[{"value":1}]}',
    },
  ],
};

it('loads and displays workflow and activity run details on demand', async () => {
  const loadDetail = jest.fn().mockResolvedValue(detail);
  render(<TemporalRunList runs={[run]} error={null} loadDetail={loadDetail} />);

  expect(screen.getByText('Completed')).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: /seizu_configured_workflow/ }),
  );

  expect(await screen.findByText('Activities (1)')).toBeInTheDocument();
  expect(loadDetail).toHaveBeenCalledWith(run);
  expect(screen.getByText('execute_configured_query')).toBeInTheDocument();
  expect(
    screen.getByText('stage:1:activity:1:findings:query'),
  ).toBeInTheDocument();

  fireEvent.click(
    screen.getByRole('button', { name: /execute_configured_query/ }),
  );
  expect(await screen.findByText('1 of 3')).toBeInTheDocument();
  expect(screen.getByText('Result')).toBeInTheDocument();
});

it('shows a clear empty state', () => {
  render(<TemporalRunList runs={[]} error={null} loadDetail={jest.fn()} />);
  expect(
    screen.getByText('No Temporal runs are visible yet.'),
  ).toBeInTheDocument();
});
