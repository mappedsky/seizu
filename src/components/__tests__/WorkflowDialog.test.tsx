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
  query: [
    {
      name: 'cypher',
      label: 'Cypher',
      type: 'text',
      required: true,
    },
    {
      name: 'parameters',
      label: 'Query parameters',
      type: 'parameters',
    },
    {
      name: 'max_rows',
      label: 'Max rows',
      type: 'number',
    },
  ],
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
  schedule: { type: 'interval', interval_minutes: 15 },
  watch_scans: [],
  enabled: true,
  stages: [
    {
      activities: [
        {
          type: 'query',
          input: null,
          output: 'findings',
          parameters: {
            cypher: 'RETURN $severity AS severity',
            parameters: [{ name: 'severity', value: 'CRITICAL' }],
            max_rows: 50,
          },
        },
      ],
    },
    {
      activities: [
        {
          type: 'log',
          input: 'findings',
          output: 'first_notification',
          parameters: { message: 'first' },
        },
        {
          type: 'log',
          input: 'findings',
          output: 'second_notification',
          parameters: { message: 'second' },
        },
      ],
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

it('edits staged activities and serializes query parameters and output references', async () => {
  const onSave = jest.fn().mockResolvedValue(undefined);
  render(
    <WorkflowDialog
      open
      initial={initial}
      activityTypes={['query', 'log']}
      activitySchemas={schemas}
      activityDefinitions={{
        query: {
          description: 'Runs a query.',
          input_required: false,
          input_schema: {},
          output_schema: { type: 'array' },
          config_fields: schemas.query,
        },
        log: {
          description: 'Writes rows to the log.',
          input_required: true,
          input_schema: { type: 'array' },
          output_schema: { type: 'object' },
          config_fields: schemas.log,
        },
      }}
      dependentSchemas={{}}
      onClose={jest.fn()}
      onSave={onSave}
    />,
  );

  expect(screen.getAllByDisplayValue('findings')[0]).toBeInTheDocument();
  expect(
    screen.getByDisplayValue('RETURN $severity AS severity'),
  ).toBeInTheDocument();
  expect(screen.getAllByRole('group', { name: /Stage/ })).toHaveLength(2);
  expect(
    screen
      .getAllByRole('button', { name: 'Move activity 1 to previous stage' })
      .some((button) => !button.hasAttribute('disabled')),
  ).toBe(true);

  const messages = [
    screen.getByDisplayValue('first'),
    screen.getByDisplayValue('second'),
  ];
  fireEvent.change(messages[0], { target: { value: 'updated first' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save workflow' }));

  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  const request = onSave.mock.calls[0][0];
  expect(request.stages[0].activities[0].parameters.parameters).toEqual([
    { name: 'severity', value: 'CRITICAL' },
  ]);
  expect(request.stages[0].activities[0].parameters.max_rows).toBe(50);
  expect(
    request.stages[1].activities.map(
      (activity: { parameters: { message: string } }) =>
        activity.parameters.message,
    ),
  ).toEqual(['updated first', 'second']);
  expect(request.stages[1].activities[0].input).toBe('findings');
  expect(request.stages[1].activities[0].output).toBe('first_notification');
});

it('shows activity descriptions in accessible tooltips instead of inline help text', async () => {
  render(
    <WorkflowDialog
      open
      initial={initial}
      activityTypes={['query', 'log']}
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
