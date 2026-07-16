import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import WorkflowDialog from 'src/components/WorkflowDialog';
import { ActionConfigFieldDef } from 'src/config.context';
import { WorkflowItem } from 'src/hooks/useWorkflowsApi';

jest.mock('src/hooks/useSyncMetadataValues', () => ({
  useSyncMetadataValues: () => ({
    grouptypes: [],
    syncedtypes: [],
    groupids: [],
  }),
}));

afterEach(cleanup);

const schemas: Record<string, ActionConfigFieldDef[]> = {
  log: [
    {
      name: 'message',
      label: 'Message',
      type: 'string',
      required: true,
      description: 'Message written for every matching query result.',
    },
  ],
};

const initial: WorkflowItem = {
  workflow_id: 'workflow-1',
  name: 'Notify',
  inputs: {
    findings: {
      type: 'query',
      cypher: 'RETURN $severity AS severity',
      parameters: [{ name: 'severity', value: 'CRITICAL' }],
      max_rows: 50,
    },
  },
  schedule: { type: 'interval', interval_minutes: 15 },
  watch_scans: [],
  enabled: true,
  activities: [
    {
      type: 'log',
      input: 'findings',
      parameters: { message: 'first' },
    },
    {
      type: 'log',
      input: 'findings',
      parameters: { message: 'second' },
    },
  ],
  current_version: 2,
  created_at: '2026-01-01T00:00:00+00:00',
  updated_at: '2026-01-02T00:00:00+00:00',
  created_by: 'user-1',
  updated_by: 'user-1',
  last_run_status: null,
  last_run_at: null,
  last_errors: [],
  schedule_sync_status: 'synced',
  schedule_sync_error: null,
  schedule_synced_at: '2026-01-02T00:00:00+00:00',
};

it('edits named inputs and serializes ordered activity parameters', async () => {
  const onSave = jest.fn().mockResolvedValue(undefined);
  render(
    <WorkflowDialog
      open
      initial={initial}
      activityTypes={['log']}
      activitySchemas={schemas}
      dependentSchemas={{}}
      onClose={jest.fn()}
      onSave={onSave}
    />,
  );

  expect(screen.getAllByDisplayValue('findings')[0]).toBeInTheDocument();
  expect(
    screen.getByDisplayValue('RETURN $severity AS severity'),
  ).toBeInTheDocument();
  expect(
    screen.getAllByRole('button', { name: /Reorder activity/ }),
  ).toHaveLength(2);

  const messages = [
    screen.getByDisplayValue('first'),
    screen.getByDisplayValue('second'),
  ];
  fireEvent.change(messages[0], { target: { value: 'updated first' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save workflow' }));

  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  const request = onSave.mock.calls[0][0];
  expect(request.inputs.findings.parameters).toEqual([
    { name: 'severity', value: 'CRITICAL' },
  ]);
  expect(request.inputs.findings.max_rows).toBe(50);
  expect(
    request.activities.map(
      (activity: { parameters: { message: string } }) =>
        activity.parameters.message,
    ),
  ).toEqual(['updated first', 'second']);
});

it('shows activity descriptions in accessible tooltips instead of inline help text', async () => {
  render(
    <WorkflowDialog
      open
      initial={initial}
      activityTypes={['log']}
      activitySchemas={schemas}
      dependentSchemas={{}}
      onClose={jest.fn()}
      onSave={jest.fn().mockResolvedValue(undefined)}
    />,
  );

  expect(
    screen.queryByText('Message written for every matching query result.'),
  ).not.toBeInTheDocument();

  fireEvent.mouseOver(
    screen.getAllByRole('button', { name: 'Help for Message' })[0],
  );

  expect(
    await screen.findByRole('tooltip', {
      name: 'Message written for every matching query result.',
    }),
  ).toBeInTheDocument();
});
