import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
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

function activity(
  activity_id: string,
  activity_type: string,
  status = 'completed',
) {
  return {
    activity_id,
    activity_type,
    status,
    attempts: 1,
    maximum_attempts: 3,
    scheduled_at: '2026-07-17T01:00:00Z',
    started_at: '2026-07-17T01:00:01Z',
    closed_at: '2026-07-17T01:00:02Z',
    retry_state: null,
    failure: null,
    last_attempt_failure: null,
    input_preview: null,
    result_preview: null,
  };
}

const detail: WorkflowRunDetail = {
  ...run,
  failure: null,
  activities: [
    activity(
      'seizu-configured-workflow-mutex:workflow-1',
      'seizu_configured_workflow_execution',
    ),
    activity('1', 'load_configured_workflow'),
    {
      ...activity(
        'stage:1:activity:1:findings:query',
        'execute_configured_query',
      ),
      input_preview: '{"cypher":"RETURN 1"}',
      result_preview: '{"output_id":"findings","value":[{"value":1}]}',
    },
    activity(
      'stage:2:activity:1:fixes:build_input',
      'build_code_workflow_input',
    ),
    activity(
      'seizu-workflow:workflow-1:stage:2:activity:1:fixes:cve_dependency_remediation',
      'cve_dependency_remediation',
      'failed',
    ),
    activity('4', 'record_configured_workflow_result'),
  ],
};

const runRow = () => screen.getByRole('button', { name: /Completed/ });

it("groups a run's activities by stage and folds engine activities away", async () => {
  const loadDetail = jest.fn().mockResolvedValue(detail);
  render(<TemporalRunList runs={[run]} error={null} loadDetail={loadDetail} />);

  expect(screen.getByText('Completed')).toBeInTheDocument();
  fireEvent.click(runRow());

  expect(await screen.findByText('Stage 1')).toBeInTheDocument();
  expect(loadDetail).toHaveBeenCalledWith(run);
  expect(screen.getByText('Stage 2')).toBeInTheDocument();
  expect(screen.getByText('query')).toBeInTheDocument();
  // The helper activity folds into the step it serves, which takes the
  // child workflow's name and its failure.
  const failedTile = screen.getByRole('button', {
    name: /Failed.*cve_dependency_remediation/,
  });
  expect(failedTile).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByText('build_input')).not.toBeInTheDocument();
  // The failing step is opened for the reader.
  expect(screen.getByText('build_code_workflow_input')).toBeInTheDocument();

  expect(screen.queryByText('Load definition')).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Engine activities (3)' }),
  );
  expect(screen.getByText('Load definition')).toBeInTheDocument();
  expect(screen.getByText('Workflow lock')).toBeInTheDocument();
  expect(screen.getByText('Record result')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /→ findings/ }));
  expect(await screen.findByText('1 of 3')).toBeInTheDocument();
  expect(screen.getByText('Result')).toBeInTheDocument();
});

it('explains a run that never reached its stages', async () => {
  const waiting = {
    ...detail,
    status: 'waiting',
    activities: [
      activity(
        'seizu-configured-workflow-mutex:workflow-1',
        'seizu_configured_workflow_execution',
        'waiting',
      ),
    ],
  };
  render(
    <TemporalRunList
      runs={[{ ...run, status: 'waiting' }]}
      error={null}
      loadDetail={jest.fn().mockResolvedValue(waiting)}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /Waiting/ }));
  expect(
    await screen.findByText(
      'Waiting for an earlier run of this workflow to finish.',
    ),
  ).toBeInTheDocument();
  // With no stages to show, the engine activities are the whole story.
  expect(screen.getByText('Workflow lock')).toBeInTheDocument();
});

it('shows a clear empty state', () => {
  render(<TemporalRunList runs={[]} error={null} loadDetail={jest.fn()} />);
  expect(screen.getByText('No runs yet.')).toBeInTheDocument();
});

it('allows a waiting workflow run to be canceled', async () => {
  const waitingRun = { ...run, status: 'waiting' };
  let resolveCancel: (() => void) | undefined;
  const cancelRun = jest.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveCancel = resolve;
      }),
  );
  render(
    <TemporalRunList
      runs={[waitingRun]}
      error={null}
      loadDetail={jest.fn().mockResolvedValue({ ...detail, status: 'waiting' })}
      cancelRun={cancelRun}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: /Waiting/ }));
  await screen.findByText('Stage 1');
  const cancelButton = screen.getByRole('button', {
    name: 'Cancel waiting run',
  });
  fireEvent.click(cancelButton);

  expect(cancelRun).toHaveBeenCalledWith(waitingRun);
  const cancelingButton = screen.getByRole('button', { name: /Canceling/ });
  expect(cancelingButton).toBeDisabled();
  expect(
    within(cancelingButton).getByRole('progressbar', { name: 'Loading' }),
  ).toBeVisible();
  expect(screen.getByText('Waiting')).toBeInTheDocument();

  await act(async () => resolveCancel?.());

  await waitFor(() => {
    expect(screen.getByText('Cancel Requested')).toBeInTheDocument();
  });
  expect(
    screen.queryByRole('button', { name: /Cancel waiting run|Canceling/ }),
  ).not.toBeInTheDocument();
});

it('says when no worker has picked a run up', async () => {
  render(
    <TemporalRunList
      runs={[{ ...run, status: 'running', close_time: null }]}
      error={null}
      loadDetail={jest
        .fn()
        .mockResolvedValue({ ...detail, status: 'running', activities: [] })}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /Running/ }));
  expect(
    await screen.findByText(/No worker has picked up this run yet/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /Engine activities/ }),
  ).not.toBeInTheDocument();
});

it('reads a run only once opened, and polls it until it finishes', async () => {
  jest.useFakeTimers();
  try {
    const running = {
      ...detail,
      status: 'running',
      activities: [
        activity(
          'stage:1:activity:1:findings:query',
          'execute_configured_query',
          'running',
        ),
      ],
    };
    const loadDetail = jest
      .fn()
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(running)
      .mockResolvedValue({ ...detail, status: 'completed' });
    const activeRun = { ...run, status: 'running' };
    render(
      <TemporalRunList
        runs={[activeRun]}
        error={null}
        loadDetail={loadDetail}
      />,
    );
    // A collapsed run is never read.
    await act(async () => {
      jest.advanceTimersByTime(15000);
    });
    expect(loadDetail).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Running/ }));
    expect(await screen.findByText('Stage 1')).toBeInTheDocument();
    expect(loadDetail).toHaveBeenCalledTimes(1);

    await act(async () => {
      jest.advanceTimersByTime(5000);
    });
    expect(loadDetail).toHaveBeenCalledTimes(2);
    await act(async () => {
      jest.advanceTimersByTime(5000);
    });
    expect(loadDetail).toHaveBeenCalledTimes(3);
    // The run reached a final status, so the poll stops.
    expect(await screen.findByText('Stage 2')).toBeInTheDocument();
    await act(async () => {
      jest.advanceTimersByTime(20000);
    });
    expect(loadDetail).toHaveBeenCalledTimes(3);
  } finally {
    jest.useRealTimers();
  }
});

it('stops polling when an active run is collapsed', async () => {
  jest.useFakeTimers();
  try {
    const loadDetail = jest
      .fn()
      .mockResolvedValue({ ...detail, status: 'running' });
    render(
      <TemporalRunList
        runs={[{ ...run, status: 'running' }]}
        error={null}
        loadDetail={loadDetail}
      />,
    );
    const row = screen.getByRole('button', { name: /Running/ });
    fireEvent.click(row);
    await screen.findByText('Stage 1');
    expect(loadDetail).toHaveBeenCalledTimes(1);

    fireEvent.click(row);
    await act(async () => {
      jest.advanceTimersByTime(20000);
    });
    expect(loadDetail).toHaveBeenCalledTimes(1);
  } finally {
    jest.useRealTimers();
  }
});
