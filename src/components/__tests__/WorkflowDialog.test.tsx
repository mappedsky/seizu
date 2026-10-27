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

  // The first activity opens in the inspector; the flow shows every stage.
  expect(screen.getByDisplayValue('findings')).toBeInTheDocument();
  expect(
    screen.getByDisplayValue('RETURN $severity AS severity'),
  ).toBeInTheDocument();
  expect(screen.getAllByRole('group', { name: /Stage/ })).toHaveLength(2);
  expect(screen.getByRole('combobox', { name: 'Input' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(
    screen.getByRole('button', { name: 'Move activity 1 to previous stage' }),
  ).toBeDisabled();

  fireEvent.click(
    screen.getByRole('button', { name: /findings → first_notification/ }),
  );
  expect(screen.getByRole('combobox', { name: 'Input' })).not.toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(
    screen.getByRole('button', { name: 'Move activity 1 to previous stage' }),
  ).toBeEnabled();
  fireEvent.change(screen.getByDisplayValue('first'), {
    target: { value: 'updated first' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: /findings → second_notification/ }),
  );
  expect(screen.getByDisplayValue('second')).toBeInTheDocument();
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

  fireEvent.click(
    screen.getByRole('button', { name: /Runs when started by hand/ }),
  );
  expect(screen.getByRole('radio', { name: 'Manual' })).toBeChecked();

  fireEvent.click(
    screen.getByRole('button', { name: /After every stage succeeds/ }),
  );
  expect(
    screen.getByText('No workflows run after this one.'),
  ).toBeInTheDocument();

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
  // The flow names what runs next.
  expect(
    screen.getByRole('button', { name: /Another workflow/ }),
  ).toBeInTheDocument();
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
  expect(await screen.findByRole('alert')).toHaveTextContent(
    "Activity 'sync' requires at least one intel module.",
  );
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

  fireEvent.click(
    screen.getByRole('button', { name: /findings → first_notification/ }),
  );
  fireEvent.mouseOver(screen.getByRole('button', { name: 'Help for Message' }));

  expect(
    await screen.findByRole('tooltip', {
      name: 'Message written for every matching query result.',
    }),
  ).toBeInTheDocument();

  expect(
    screen
      .getByRole('button', { name: 'Help for Output name' })
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

  // A new stage arrives with its first activity, opened for editing.
  fireEvent.click(screen.getByRole('button', { name: 'Add stage' }));
  expect(screen.getByDisplayValue('stage_1_activity_1')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Add activity' }));
  expect(screen.getByDisplayValue('stage_1_activity_2')).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: /No input → stage_1_activity_1/ }),
  ).toBeInTheDocument();

  expect(screen.getByRole('combobox', { name: 'Input' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  fireEvent.mouseOver(
    screen.getByLabelText('Input unavailable', { selector: 'span' }),
  );
  expect(
    await screen.findByRole('tooltip', {
      name: 'Inputs can only reference outputs from earlier stages; none are available here.',
    }),
  ).toBeInTheDocument();
});

it('marks every problem after a save attempt and opens the first one', async () => {
  const onSave = jest.fn().mockResolvedValue(undefined);
  render(
    <WorkflowDialog
      open
      initial={null}
      activityTypes={['query', 'log']}
      activitySchemas={schemas}
      onClose={jest.fn()}
      onSave={onSave}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: 'Add stage' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add activity' }));
  // Nothing is marked while the workflow is still being built.
  expect(
    screen.queryByText('Choose an activity type.'),
  ).not.toBeInTheDocument();

  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
    target: { value: 'Alerts' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: /No input → stage_1_activity_2/ }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save workflow' }));

  expect(onSave).not.toHaveBeenCalled();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Choose an activity type. · 1 more marked below',
  );
  expect(screen.getAllByText('Choose an activity type.')).toHaveLength(3);
  // The first problem's activity is opened for fixing.
  expect(
    screen.getByRole('button', { name: /No input → stage_1_activity_1/ }),
  ).toHaveAttribute('aria-pressed', 'true');
});

it('closes straight away when nothing changed, and asks before discarding edits', async () => {
  const onClose = jest.fn();
  const { unmount } = render(
    <WorkflowDialog
      open
      initial={initial}
      activityTypes={['query', 'log']}
      activitySchemas={schemas}
      onClose={onClose}
      onSave={jest.fn().mockResolvedValue(undefined)}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Close editor' }));
  expect(onClose).toHaveBeenCalledTimes(1);
  unmount();

  render(
    <WorkflowDialog
      open
      initial={initial}
      activityTypes={['query', 'log']}
      activitySchemas={schemas}
      onClose={onClose}
      onSave={jest.fn().mockResolvedValue(undefined)}
    />,
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
    target: { value: 'Renamed' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(
    await screen.findByText('Discard unsaved changes?'),
  ).toBeInTheDocument();
  expect(onClose).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
  expect(onClose).toHaveBeenCalledTimes(2);
});

describe('WorkflowDialog on a phone', () => {
  const original = window.matchMedia;
  beforeEach(() => {
    // Below `md`: down() queries are max-width; reduced motion is not set.
    window.matchMedia = ((query: string) => ({
      matches: query.includes('max-width'),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
  });
  afterEach(() => {
    window.matchMedia = original;
    cleanup();
  });

  it('slides the inspector over the flow and back', async () => {
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
    const inspector = () =>
      document.querySelector('aside[aria-label="Inspector"]');

    // The flow is the first screen; nothing is being edited yet.
    expect(inspector()).toBeNull();

    const tile = screen.getByRole('button', {
      name: /findings → first_notification/,
    });
    fireEvent.click(tile);
    expect(inspector()).not.toBeNull();
    expect(screen.getByDisplayValue('first')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Back to workflow'));
    // Booleans, not elements: a retried failure would otherwise pretty-print
    // the whole inspector on every poll.
    await waitFor(() => expect(inspector() === null).toBe(true));
    await waitFor(() => expect(document.activeElement === tile).toBe(true));
  });
});
