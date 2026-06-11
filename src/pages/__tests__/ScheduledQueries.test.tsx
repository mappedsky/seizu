import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ScheduledQueries from 'src/pages/ScheduledQueries';
import * as scheduledQueriesApiModule from 'src/hooks/useScheduledQueriesApi';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/hooks/useScheduledQueriesApi', () => ({
  useScheduledQueriesList: jest.fn(),
  useScheduledQueriesMutations: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;
const mockUseScheduledQueriesList =
  scheduledQueriesApiModule.useScheduledQueriesList as unknown as jest.Mock;
const mockUseScheduledQueriesMutations =
  scheduledQueriesApiModule.useScheduledQueriesMutations as unknown as jest.Mock;
const theme = createTheme();

const SCHEDULED_QUERY: scheduledQueriesApiModule.ScheduledQueryItem = {
  scheduled_query_id: 'sq1',
  name: 'Recent CVEs',
  cypher: 'MATCH (c:CVE) RETURN c LIMIT 10',
  params: [],
  frequency: 60,
  watch_scans: [],
  enabled: true,
  actions: [{ action_type: 'slack', action_config: {} }],
  current_version: 4,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  created_by: 'alice',
  updated_by: 'bob',
  last_run_status: 'success',
  last_run_at: '2026-01-02T01:00:00Z',
  last_errors: [],
};

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/scheduled-queries']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route path="/app/scheduled-queries" element={<>{children}</>} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('ScheduledQueries', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        scheduled_query_action_types: ['slack'],
        scheduled_query_action_schemas: {},
      }),
    } as Response);
    mockUsePermissions.mockReturnValue((permission: string) =>
      permission.startsWith('scheduled_queries:'),
    );
    mockUseScheduledQueriesList.mockReturnValue({
      scheduledQueries: [SCHEDULED_QUERY],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseScheduledQueriesMutations.mockReturnValue({
      createScheduledQuery: jest.fn(),
      updateScheduledQuery: jest.fn(),
      deleteScheduledQuery: jest.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    jest.restoreAllMocks();
  });

  it('renders scheduled query list columns and fetches action config on view', async () => {
    render(<ScheduledQueries />, { wrapper: Wrapper });

    expect(screen.getByText('Last updated')).toBeInTheDocument();
    expect(screen.getByText('Updated by')).toBeInTheDocument();

    expect(screen.getByText('Recent CVEs')).toBeInTheDocument();
    expect(screen.getByText('Every 60 min')).toBeInTheDocument();
    expect(screen.getByText('slack')).toBeInTheDocument();
    expect(screen.getByText('v4')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('/api/v1/config'),
    );
  });

  it('renders the action config field warning in the edit dialog', async () => {
    const warning =
      'This workflow runs headlessly WITHOUT interactive confirmation.';
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        scheduled_query_action_types: ['temporal'],
        scheduled_query_action_schemas: {
          temporal: [
            {
              name: 'accept_confirmation_bypass',
              label: 'I accept',
              type: 'boolean',
              required: true,
              default: false,
              warning,
            },
          ],
        },
      }),
    } as Response);
    mockUseScheduledQueriesList.mockReturnValue({
      scheduledQueries: [
        {
          ...SCHEDULED_QUERY,
          actions: [
            {
              action_type: 'temporal',
              action_config: { workflow: 'cve_repo_report' },
            },
          ],
        },
      ],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });

    render(<ScheduledQueries />, { wrapper: Wrapper });
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('/api/v1/config'),
    );

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(await screen.findByText('Edit'));

    expect(await screen.findByText(warning)).toBeInTheDocument();
    expect(screen.getByLabelText('I accept *')).toBeInTheDocument();
  });

  it('hides permission-gated action types from users without the permission', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        scheduled_query_action_types: ['slack', 'agent_chat'],
        scheduled_query_action_schemas: {},
        scheduled_query_action_permissions: {
          agent_chat: 'chat:bypass_permissions',
        },
      }),
    } as Response);

    render(<ScheduledQueries />, { wrapper: Wrapper });
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('/api/v1/config'),
    );

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(await screen.findByText('Edit'));

    // The mocked permissions grant only scheduled_queries:*, so agent_chat
    // must be filtered out of the action type select (identified by its
    // current value, the existing slack action).
    const comboboxes = await screen.findAllByRole('combobox');
    const actionTypeSelect = comboboxes.find((c) => c.textContent === 'slack');
    expect(actionTypeSelect).toBeDefined();
    fireEvent.mouseDown(actionTypeSelect as HTMLElement);
    const options = await screen.findAllByRole('option');
    const labels = options.map((o) => o.textContent);
    expect(labels).toContain('slack');
    expect(labels).not.toContain('agent_chat');
  });
});
