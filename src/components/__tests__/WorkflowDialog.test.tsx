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

// MUI dialog/autocomplete transitions are substantially slower when this file
// runs as part of the complete frontend suite than in isolation.
jest.setTimeout(30_000);

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
  trigger_workflows: [],
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
      onClose={jest.fn()}
      onSave={onSave}
    />,
  );

  expect(screen.getAllByDisplayValue('findings')[0]).toBeInTheDocument();
  expect(
    screen.getByDisplayValue('RETURN $severity AS severity'),
  ).toBeInTheDocument();
  expect(screen.getAllByRole('group', { name: /Stage/ })).toHaveLength(2);
  const inputSelectors = screen.getAllByRole('combobox', { name: 'Input' });
  expect(inputSelectors[0]).toHaveAttribute('aria-disabled', 'true');
  expect(inputSelectors[1]).not.toHaveAttribute('aria-disabled', 'true');
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

it('supports manual workflows and post-completion workflow triggers', async () => {
  const onSave = jest.fn().mockResolvedValue(undefined);
  const downstream: WorkflowItem = {
    ...initial,
    workflow_id: 'workflow-2',
    name: 'Downstream',
  };
  const another: WorkflowItem = {
    ...initial,
    workflow_id: 'workflow-3',
    name: 'Another workflow',
  };
  render(
    <WorkflowDialog
      open
      initial={{ ...initial, schedule: null }}
      activityTypes={['query', 'log']}
      activitySchemas={schemas}
      workflowOptions={[initial, downstream, another]}
      onClose={jest.fn()}
      onSave={onSave}
    />,
  );

  expect(screen.getByRole('radio', { name: 'Manual' })).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'Add workflow' }));
  const firstWorkflow = screen.getByRole('combobox', { name: 'Workflow 1' });
  fireEvent.change(firstWorkflow, { target: { value: 'Down' } });
  fireEvent.click(await screen.findByRole('option', { name: 'Downstream' }));

  fireEvent.click(screen.getByRole('button', { name: 'Add workflow' }));
  const secondWorkflow = screen.getByRole('combobox', { name: 'Workflow 2' });
  fireEvent.mouseDown(secondWorkflow);
  expect(
    screen.queryByRole('option', { name: 'Downstream' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    await screen.findByRole('option', { name: 'Another workflow' }),
  );

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove triggered workflow 1' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save workflow' }));

  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave.mock.calls[0][0]).toMatchObject({
    schedule: null,
    watch_scans: [],
    trigger_workflows: ['workflow-3'],
  });
});

const cartographySchemas: Record<string, ActionConfigFieldDef[]> = {
  cartography_sync: [
    {
      name: 'module_runs',
      label: 'Intel modules',
      type: 'module_runs',
      required: true,
      options: ['analysis', 'aws', 'create-indexes', 'github'],
      item_schemas: {
        analysis: [],
        aws: [
          {
            name: 'aws_sync_all_profiles',
            label: 'Aws sync all profiles',
            type: 'boolean',
            default: true,
          },
        ],
        'create-indexes': [],
        github: [],
      },
    },
    {
      name: 'stop_on_failure',
      label: 'Stop on failure',
      type: 'boolean',
      default: false,
    },
  ],
};

const cartographyInitial: WorkflowItem = {
  ...initial,
  stages: [
    {
      activities: [
        {
          type: 'cartography_sync',
          input: null,
          output: 'sync',
          parameters: {
            module_runs: [
              { module: 'create-indexes', params: {} },
              { module: 'github', params: {} },
            ],
          },
        },
      ],
    },
  ],
};

it('renders and serializes an ordered intel module list for module_runs fields', async () => {
  const onSave = jest.fn().mockResolvedValue(undefined);
  render(
    <WorkflowDialog
      open
      initial={cartographyInitial}
      activityTypes={['cartography_sync']}
      activitySchemas={cartographySchemas}
      onClose={jest.fn()}
      onSave={onSave}
    />,
  );

  const moduleSelects = screen.getAllByRole('combobox', {
    name: 'Intel module',
  });
  expect(moduleSelects).toHaveLength(2);
  expect(moduleSelects[0]).toHaveTextContent('create-indexes');
  expect(moduleSelects[1]).toHaveTextContent('github');

  fireEvent.click(screen.getByRole('button', { name: 'Add intel module' }));
  const added = screen.getAllByRole('combobox', { name: 'Intel module' })[2];
  fireEvent.mouseDown(added);
  fireEvent.click(await screen.findByRole('option', { name: 'aws' }));
  // Selecting a module seeds its param defaults, rendered as a sub-form.
  expect(
    screen.getByRole('checkbox', { name: 'Aws sync all profiles' }),
  ).toBeChecked();

  fireEvent.click(
    screen.getByRole('button', { name: 'Move intel module 3 up' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save workflow' }));

  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  const request = onSave.mock.calls[0][0];
  expect(request.stages[0].activities[0].parameters.module_runs).toEqual([
    { module: 'create-indexes', params: {} },
    { module: 'aws', params: { aws_sync_all_profiles: true } },
    { module: 'github', params: {} },
  ]);
});

it('requires at least one intel module when the field is required', async () => {
  const onSave = jest.fn().mockResolvedValue(undefined);
  render(
    <WorkflowDialog
      open
      initial={{
        ...cartographyInitial,
        stages: [
          {
            activities: [
              {
                type: 'cartography_sync',
                input: null,
                output: 'sync',
                parameters: { module_runs: [] },
              },
            ],
          },
        ],
      }}
      activityTypes={['cartography_sync']}
      activitySchemas={cartographySchemas}
      onClose={jest.fn()}
      onSave={onSave}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: 'Save workflow' }));
  expect(
    await screen.findByText(
      "Activity 'sync' requires at least one intel module.",
    ),
  ).toBeInTheDocument();
  expect(onSave).not.toHaveBeenCalled();
});

it('shows activity descriptions in accessible tooltips instead of inline help text', async () => {
  render(
    <WorkflowDialog
      open
      initial={initial}
      activityTypes={['query', 'log']}
      activitySchemas={schemas}
      onClose={jest.fn()}
      onSave={jest.fn().mockResolvedValue(undefined)}
    />,
  );

  expect(
    screen.queryByText('Message written for every matching query result.'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText('Produces any JSON value.'),
  ).not.toBeInTheDocument();

  fireEvent.mouseOver(
    screen.getAllByRole('button', { name: 'Help for Message' })[0],
  );

  expect(
    await screen.findByRole('tooltip', {
      name: 'Message written for every matching query result.',
    }),
  ).toBeInTheDocument();

  expect(
    screen
      .getAllByRole('button', { name: 'Help for Output name' })[0]
      .getAttribute('title'),
  ).toBe('Produces any JSON value.');
});

it('generates unique output names and explains disabled inputs', async () => {
  render(
    <WorkflowDialog
      open
      initial={null}
      activityTypes={['query', 'log']}
      activitySchemas={schemas}
      onClose={jest.fn()}
      onSave={jest.fn().mockResolvedValue(undefined)}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: 'Add stage' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add activity' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add activity' }));

  expect(screen.getByDisplayValue('stage_1_activity_1')).toBeInTheDocument();
  expect(screen.getByDisplayValue('stage_1_activity_2')).toBeInTheDocument();

  const inputs = screen.getAllByRole('combobox', { name: 'Input' });
  expect(inputs).toHaveLength(2);
  inputs.forEach((input) =>
    expect(input).toHaveAttribute('aria-disabled', 'true'),
  );

  fireEvent.mouseOver(
    screen.getAllByLabelText('Input unavailable', {
      selector: 'span',
    })[0],
  );
  expect(
    await screen.findByRole('tooltip', {
      name: 'Inputs can only reference outputs from earlier stages; none are available here.',
    }),
  ).toBeInTheDocument();
});
