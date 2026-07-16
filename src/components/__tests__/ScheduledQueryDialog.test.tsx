import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import ScheduledQueryDialog from 'src/components/ScheduledQueryDialog';
import {
  ActionConfigDependentSchema,
  ActionConfigFieldDef,
} from 'src/config.context';
import { ScheduledQueryItem } from 'src/hooks/useScheduledQueriesApi';

jest.mock('src/hooks/useSyncMetadataValues', () => ({
  useSyncMetadataValues: () => ({
    grouptypes: [],
    syncedtypes: [],
    groupids: [],
  }),
}));

afterEach(cleanup);

const actionSchemas: Record<string, ActionConfigFieldDef[]> = {
  temporal: [
    {
      name: 'workflow',
      label: 'Workflow',
      type: 'select',
      required: true,
      options: ['cartography_sync', 'cve_repo_report'],
    },
  ],
};

const dependentSchemas: Record<string, ActionConfigDependentSchema> = {
  temporal: {
    discriminator: 'workflow',
    schemas: {
      cartography_sync: [
        {
          name: 'modules',
          label: 'Modules',
          type: 'string_list',
        },
        {
          name: 'timeout_minutes',
          label: 'Per-module timeout (minutes)',
          type: 'number',
          default: 60,
        },
      ],
    },
  },
};

function item(actionConfig: Record<string, unknown>): ScheduledQueryItem {
  return {
    scheduled_query_id: 'sq-1',
    name: 'Sync things',
    cypher: 'RETURN 1',
    params: [],
    frequency: null,
    schedule: { type: 'interval', interval_minutes: 60 },
    watch_scans: [],
    enabled: true,
    actions: [{ action_type: 'temporal', action_config: actionConfig }],
    current_version: 1,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    created_by: 'user-1',
    updated_by: null,
    last_run_status: null,
    last_run_at: null,
    last_errors: [],
  };
}

function renderDialog(
  actionConfig: Record<string, unknown>,
  onSave = jest.fn().mockResolvedValue(undefined),
) {
  render(
    <ScheduledQueryDialog
      open
      onClose={jest.fn()}
      onSave={onSave}
      initial={item(actionConfig)}
      actionTypes={['temporal', 'log']}
      actionSchemas={actionSchemas}
      dependentSchemas={dependentSchemas}
    />,
  );
  return onSave;
}

describe('ScheduledQueryDialog dependent sub-schemas', () => {
  it('renders the sub-form for a workflow with config fields', () => {
    renderDialog({ workflow: 'cartography_sync', modules: ['cve'] });

    expect(screen.getByLabelText('Modules')).toBeInTheDocument();
    expect(screen.getByLabelText('Modules')).toHaveValue('cve');
    expect(
      screen.getByLabelText('Per-module timeout (minutes)'),
    ).toBeInTheDocument();
    expect(screen.getByText('cartography_sync options')).toBeInTheDocument();
  });

  it('renders no sub-form for a workflow without config fields', () => {
    renderDialog({ workflow: 'cve_repo_report' });

    expect(screen.queryByLabelText('Modules')).not.toBeInTheDocument();
  });

  it('swaps sub-schema fields and seeds defaults when the workflow changes', () => {
    renderDialog({ workflow: 'cve_repo_report' });

    // Combobox accessible names don't resolve in this test DOM, so find the
    // workflow select by the value it displays.
    const workflowSelect = () => {
      const select = screen
        .getAllByRole('combobox')
        .find((el) =>
          /cve_repo_report|cartography_sync/.test(el.textContent ?? ''),
        );
      expect(select).toBeDefined();
      return select as HTMLElement;
    };
    fireEvent.mouseDown(workflowSelect());
    fireEvent.click(screen.getByRole('option', { name: 'cartography_sync' }));

    expect(screen.getByLabelText('Modules')).toBeInTheDocument();
    expect(screen.getByLabelText('Per-module timeout (minutes)')).toHaveValue(
      60,
    );

    // Switching away drops the sub-schema fields again.
    fireEvent.mouseDown(workflowSelect());
    fireEvent.click(screen.getByRole('option', { name: 'cve_repo_report' }));
    expect(screen.queryByLabelText('Modules')).not.toBeInTheDocument();
  });

  it('serializes sub-schema string_list fields on save', async () => {
    const onSave = renderDialog({
      workflow: 'cartography_sync',
      modules: ['cve'],
    });

    fireEvent.change(screen.getByLabelText('Modules'), {
      target: { value: 'cve, github' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const req = onSave.mock.calls[0][0];
    expect(req.actions[0].action_config.modules).toEqual(['cve', 'github']);
    expect(req.actions[0].action_config.workflow).toBe('cartography_sync');
  });
});
