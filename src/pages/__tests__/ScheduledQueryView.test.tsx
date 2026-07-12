import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ScheduledQueryView from 'src/pages/ScheduledQueryView';
import * as scheduledQueriesApiModule from 'src/hooks/useScheduledQueriesApi';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/hooks/useScheduledQueriesApi', () => ({
  useScheduledQuery: jest.fn(),
  useScheduledQueriesMutations: jest.fn(),
  useScheduledQueryWorkflowRuns: jest.fn(),
  useWorkflowRunDetail: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

jest.mock('react-helmet', () => ({
  Helmet: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;
const mockUseScheduledQuery =
  scheduledQueriesApiModule.useScheduledQuery as unknown as jest.Mock;
const mockUseScheduledQueriesMutations =
  scheduledQueriesApiModule.useScheduledQueriesMutations as unknown as jest.Mock;
const mockUseScheduledQueryWorkflowRuns =
  scheduledQueriesApiModule.useScheduledQueryWorkflowRuns as unknown as jest.Mock;
const mockUseWorkflowRunDetail =
  scheduledQueriesApiModule.useWorkflowRunDetail as unknown as jest.Mock;

const theme = createTheme();

const QUERY: scheduledQueriesApiModule.ScheduledQueryItem = {
  scheduled_query_id: 'sq1',
  name: 'Recent CVEs',
  cypher: 'MATCH (c:CVE) RETURN c LIMIT 10',
  params: [{ name: 'limit', value: '10' }],
  frequency: 60,
  schedule: null,
  watch_scans: [],
  enabled: true,
  actions: [{ action_type: 'slack', action_config: { channel: '#security' } }],
  current_version: 4,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  created_by: 'alice',
  updated_by: 'bob',
  last_run_status: 'success',
  last_run_at: '2026-01-02T01:00:00Z',
  last_errors: [],
};

function TestLocation() {
  const { pathname } = useLocation();
  return (
    <div data-testid="nav-location" style={{ display: 'none' }}>
      {pathname}
    </div>
  );
}

function makeWrapper(
  state: Record<string, unknown> = {},
  path = '/app/scheduled-queries/sq1',
) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <MemoryRouter initialEntries={[{ pathname: path, state }]}>
        <ThemeProvider theme={theme}>
          <TestLocation />
          <Routes>
            <Route
              path="/app/scheduled-queries/:id"
              element={<>{children}</>}
            />
            <Route
              path="/app/scheduled-queries"
              element={<div>list page</div>}
            />
            <Route
              path="/app/scheduled-queries/:id/history"
              element={<div>history page</div>}
            />
          </Routes>
        </ThemeProvider>
      </MemoryRouter>
    );
  };
}

const Wrapper = makeWrapper();

describe('ScheduledQueryView', () => {
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
    mockUseScheduledQuery.mockReturnValue({
      query: QUERY,
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseScheduledQueriesMutations.mockReturnValue({
      updateScheduledQuery: jest.fn(),
      deleteScheduledQuery: jest.fn(),
      runScheduledQuery: jest.fn(),
    });
    mockUseScheduledQueryWorkflowRuns.mockReturnValue({
      runs: [],
      error: null,
    });
    mockUseWorkflowRunDetail.mockReturnValue(jest.fn());
  });

  afterEach(() => {
    cleanup();
    jest.restoreAllMocks();
  });

  it('renders the query name, details panel, and content panels', async () => {
    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(
      screen.getByRole('heading', { name: 'Recent CVEs' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Every 60 min')).toBeInTheDocument();
    expect(screen.getByText('v4')).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(
      screen.getByText('MATCH (c:CVE) RETURN c LIMIT 10'),
    ).toBeInTheDocument();
    expect(screen.getByText('limit')).toBeInTheDocument();
    expect(screen.getByText('slack')).toBeInTheDocument();
    expect(screen.getByText('channel')).toBeInTheDocument();
    expect(screen.getByText('#security')).toBeInTheDocument();
  });

  it('shows the back button when fromLabel is in navigation state', async () => {
    const WrapperWithState = makeWrapper({ fromLabel: 'Scheduled Queries' });
    render(<ScheduledQueryView />, { wrapper: WrapperWithState });
    await act(async () => {});

    expect(
      screen.getByRole('button', { name: /back to scheduled queries/i }),
    ).toBeInTheDocument();
  });

  it('omits the back button without fromLabel in navigation state', async () => {
    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(
      screen.queryByRole('button', { name: /back to/i }),
    ).not.toBeInTheDocument();
  });

  it('disables the Edit button without scheduled_queries:write permission', async () => {
    mockUsePermissions.mockReturnValue(() => false);
    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled();
  });

  it('row menu contains View history and Delete actions', async () => {
    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));

    expect(
      await screen.findByRole('menuitem', { name: 'View history' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: 'Delete' }),
    ).toBeInTheDocument();
  });

  it('View history navigates to the history page with the query name as fromLabel', async () => {
    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(
      await screen.findByRole('menuitem', { name: 'View history' }),
    );

    await waitFor(() => {
      expect(screen.getByText('history page')).toBeInTheDocument();
    });
  });

  it('Run now requests a run and shows a confirmation snackbar', async () => {
    const runScheduledQuery = jest.fn().mockResolvedValue(undefined);
    mockUseScheduledQueriesMutations.mockReturnValue({
      updateScheduledQuery: jest.fn(),
      deleteScheduledQuery: jest.fn(),
      runScheduledQuery,
    });

    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Run now' }));
    await act(async () => {});

    expect(runScheduledQuery).toHaveBeenCalledWith('sq1');
    expect(
      screen.getByText(/run requested for "recent cves"/i),
    ).toBeInTheDocument();
  });

  it('delete dialog confirms and calls deleteScheduledQuery', async () => {
    const mockDelete = jest.fn().mockResolvedValue(undefined);
    mockUseScheduledQueriesMutations.mockReturnValue({
      updateScheduledQuery: jest.fn(),
      deleteScheduledQuery: mockDelete,
    });

    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));

    expect(screen.getByText(/permanently delete/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await act(async () => {});

    expect(mockDelete).toHaveBeenCalledWith('sq1');
  });

  it('navigates to the list after a successful delete', async () => {
    const mockDelete = jest.fn().mockResolvedValue(undefined);
    mockUseScheduledQueriesMutations.mockReturnValue({
      updateScheduledQuery: jest.fn(),
      deleteScheduledQuery: mockDelete,
    });

    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await act(async () => {});

    await waitFor(() => {
      expect(screen.getByText('list page')).toBeInTheDocument();
    });
  });

  it('omits the workflow runs section for queries without a temporal action', async () => {
    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(screen.queryByText('Workflow runs')).not.toBeInTheDocument();
  });

  it('shows workflow runs with per-activity detail for temporal queries', async () => {
    mockUseScheduledQuery.mockReturnValue({
      query: {
        ...QUERY,
        actions: [
          {
            action_type: 'temporal',
            action_config: { workflow: 'cve_repo_report' },
          },
        ],
      },
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseScheduledQueryWorkflowRuns.mockReturnValue({
      runs: [
        {
          workflow_id: 'seizu:cve_repo_report:sq1:2026-01-01T00:00:00+00:00',
          run_id: 'run-1',
          workflow_name: 'cve_repo_report',
          status: 'failed',
          start_time: '2026-01-01T00:00:00Z',
          close_time: '2026-01-01T01:00:00Z',
          history_length: 12,
        },
      ],
      error: null,
    });
    const fetchDetail = jest.fn().mockResolvedValue({
      workflow_id: 'seizu:cve_repo_report:sq1:2026-01-01T00:00:00+00:00',
      run_id: 'run-1',
      workflow_name: 'cve_repo_report',
      status: 'failed',
      start_time: '2026-01-01T00:00:00Z',
      close_time: '2026-01-01T01:00:00Z',
      failure: 'workflow failed',
      activities: [
        {
          activity_id: '1',
          activity_type: 'run_repo_report_chat',
          status: 'failed',
          attempts: 3,
          maximum_attempts: 3,
          scheduled_at: '2026-01-01T00:00:00Z',
          started_at: '2026-01-01T00:10:00Z',
          closed_at: '2026-01-01T00:20:00Z',
          retry_state: 'maximum_attempts_reached',
          failure: 'RuntimeError: boom',
          last_attempt_failure: null,
          input_preview: '{"repo": "org/app"}',
          result_preview: null,
        },
      ],
    });
    mockUseWorkflowRunDetail.mockReturnValue(fetchDetail);

    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(screen.getByText('Workflow runs')).toBeInTheDocument();
    const runSummary = screen.getByRole('button', {
      name: /cve_repo_report/,
    });
    expect(runSummary).toHaveTextContent('failed');

    fireEvent.click(runSummary);
    await act(async () => {});

    expect(fetchDetail).toHaveBeenCalledWith(
      'sq1',
      'seizu:cve_repo_report:sq1:2026-01-01T00:00:00+00:00',
      'run-1',
    );
    expect(await screen.findByText('workflow failed')).toBeInTheDocument();
    expect(screen.getByText('run_repo_report_chat')).toBeInTheDocument();
    expect(screen.getByText('3 attempts')).toBeInTheDocument();

    fireEvent.click(screen.getByText('run_repo_report_chat'));
    await act(async () => {});

    expect(screen.getByText('RuntimeError: boom')).toBeInTheDocument();
    expect(screen.getByText('maximum_attempts_reached')).toBeInTheDocument();
    // Input preview is pretty-printed and syntax-highlighted; the string
    // value lands in its own token span.
    expect(screen.getByText('"org/app"')).toBeInTheDocument();
  });

  it('shows an error alert when workflow runs fail to load', async () => {
    mockUseScheduledQuery.mockReturnValue({
      query: {
        ...QUERY,
        actions: [{ action_type: 'temporal', action_config: {} }],
      },
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseScheduledQueryWorkflowRuns.mockReturnValue({
      runs: null,
      error: new Error('boom'),
    });

    render(<ScheduledQueryView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(
      screen.getByText(/failed to load workflow runs/i),
    ).toBeInTheDocument();
  });

  it('shows a loading spinner while fetching', () => {
    mockUseScheduledQuery.mockReturnValue({
      query: null,
      loading: true,
      error: null,
      refresh: jest.fn(),
    });
    render(<ScheduledQueryView />, { wrapper: Wrapper });

    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows error state when fetch fails', () => {
    mockUseScheduledQuery.mockReturnValue({
      query: null,
      loading: false,
      error: new Error('oops'),
      refresh: jest.fn(),
    });
    render(<ScheduledQueryView />, { wrapper: Wrapper });

    expect(
      screen.getByText('Failed to load scheduled query'),
    ).toBeInTheDocument();
  });
});
