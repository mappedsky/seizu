import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ReportsList from 'src/pages/ReportsList';
import * as reportsApiModule from 'src/hooks/useReportsApi';
import * as permissionsModule from 'src/hooks/usePermissions';
import * as spacesApiModule from 'src/hooks/useSpacesApi';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

jest.mock('src/hooks/useSpacesApi', () => ({
  useSpacesList: jest.fn(),
  useSubspacesList: jest.fn(),
}));

const mockUsePermissionState =
  permissionsModule.usePermissionState as jest.MockedFunction<
    typeof permissionsModule.usePermissionState
  >;
const mockUseSpacesList = spacesApiModule.useSpacesList as unknown as jest.Mock;
const mockUseSubspacesList =
  spacesApiModule.useSubspacesList as unknown as jest.Mock;

const SPACES = [
  {
    space_id: 'sp1',
    name: 'Cloud Security',
    description: '',
    overview_report_id: 'ovr1',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    created_by: 'alice',
    updated_by: 'alice',
  },
];

const SUBSPACES = [
  {
    subspace_id: 'ss1',
    space_id: 'sp1',
    name: 'Network',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    created_by: 'alice',
    updated_by: 'alice',
  },
];
const theme = createTheme();

function LocationTracker() {
  const location = useLocation();
  return (
    <div data-testid="location">{`${location.pathname}${location.search}`}</div>
  );
}

const REPORTS: reportsApiModule.ReportListItem[] = [
  {
    report_id: 'r1',
    name: 'Executive Risk',
    description: '',
    current_version: 3,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    created_by: 'alice',
    updated_by: 'bob',
    access: { scope: 'public' },
    pinned: true,
    space_id: null,
    subspace_id: null,
  },
  {
    report_id: 'r2',
    name: 'Draft Findings',
    description: '',
    current_version: 1,
    created_at: '2026-01-03T00:00:00Z',
    updated_at: '2026-01-04T00:00:00Z',
    created_by: 'carol',
    updated_by: '',
    access: { scope: 'private' },
    pinned: false,
    space_id: null,
    subspace_id: null,
  },
];

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/reports']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route
            path="/app/reports"
            element={
              <>
                {children}
                <LocationTracker />
              </>
            }
          />
          <Route path="/app/reports/:reportId" element={<LocationTracker />} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('ReportsList', () => {
  let mockUseReportsList: jest.Mock;
  let mockUseDashboardReportId: jest.Mock;
  let mockUseReportsMutations: jest.Mock;
  let refreshReports: jest.Mock;
  let cloneReport: jest.Mock;
  let updateReportVisibility: jest.Mock;
  let deleteReport: jest.Mock;
  let setReportSpace: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockUseReportsList = jest.spyOn(
      reportsApiModule,
      'useReportsList',
    ) as unknown as jest.Mock;
    mockUseDashboardReportId = jest.spyOn(
      reportsApiModule,
      'useDashboardReportId',
    ) as unknown as jest.Mock;
    mockUseReportsMutations = jest.spyOn(
      reportsApiModule,
      'useReportsMutations',
    ) as unknown as jest.Mock;
    refreshReports = jest.fn();
    cloneReport = jest.fn().mockResolvedValue({
      ...REPORTS[0],
      report_id: 'clone1',
      name: 'Copy of Executive Risk',
    });
    updateReportVisibility = jest.fn();
    deleteReport = jest.fn();
    setReportSpace = jest.fn().mockResolvedValue(REPORTS[0]);
    mockUseSpacesList.mockReturnValue({
      spaces: SPACES,
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseSubspacesList.mockReturnValue({
      subspaces: SUBSPACES,
      loading: false,
      error: null,
    });
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) =>
        ['reports:write', 'reports:delete', 'reports:set_dashboard'].includes(
          permission,
        ),
      loading: false,
      currentUser: {
        user_id: 'alice',
        sub: 'alice',
        iss: 'test',
        email: 'alice@example.com',
        display_name: 'Alice',
        created_at: '2026-01-01T00:00:00Z',
        last_login: '2026-01-02T00:00:00Z',
        archived_at: null,
        permissions: [],
      },
    });
    mockUseReportsList.mockReturnValue({
      reports: REPORTS,
      total: REPORTS.length,
      page: 1,
      perPage: 500,
      loading: false,
      error: null,
      refresh: refreshReports,
    });
    mockUseDashboardReportId.mockReturnValue({
      dashboardReportId: 'r1',
      loading: false,
      refresh: jest.fn(),
    });
    mockUseReportsMutations.mockReturnValue({
      createReport: jest.fn(),
      cloneReport,
      saveReportVersion: jest.fn(),
      setDashboardReport: jest.fn(),
      pinReport: jest.fn(),
      updateReportVisibility,
      deleteReport,
      setReportSpace,
    });
  });

  afterEach(() => {
    cleanup();
    mockUseReportsList.mockRestore?.();
    mockUseDashboardReportId.mockRestore?.();
    mockUseReportsMutations.mockRestore?.();
  });

  it('renders report list columns with visibility and updated-by metadata', () => {
    render(<ReportsList />, { wrapper: Wrapper });

    expect(
      screen.getByRole('columnheader', { name: 'Visibility' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Last updated')).toBeInTheDocument();
    expect(screen.getByText('Updated by')).toBeInTheDocument();

    expect(
      screen.getByRole('link', { name: 'Executive Risk' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Public')).toBeInTheDocument();
    expect(screen.getByText('Pinned')).toBeInTheDocument();
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();

    expect(
      screen.getByRole('link', { name: 'Draft Findings' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Draft')).toBeInTheDocument();
    expect(screen.getByText('carol')).toBeInTheDocument();
  });

  // Multiple `userEvent.click` interactions trigger many React re-renders
  // through MUI menus/dialogs/router. In isolation this runs in ~2.5s, but
  // under the full test suite the per-render cost grows and the default 5s
  // timeout becomes flaky. Bump for headroom; actual work is unchanged.
  it('clones from the list view and navigates to the cloned report in edit mode', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(screen.getAllByLabelText('More actions')[0]);
    await user.click(screen.getByRole('menuitem', { name: /clone/i }));

    expect(
      screen.getByRole('textbox', { name: 'New report name' }),
    ).toHaveValue('Copy of Executive Risk');

    await user.click(screen.getByRole('button', { name: 'Clone' }));

    await waitFor(() => {
      expect(cloneReport).toHaveBeenCalledWith('r1', 'Copy of Executive Risk');
    });
    expect(refreshReports).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/app/reports/clone1?edit=true',
      );
    });
  });

  it('shows an error dialog when unpublishing is rejected', async () => {
    updateReportVisibility.mockRejectedValue(
      new Error(
        'Report must be unpinned and removed from the dashboard before it can be made private',
      ),
    );
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(screen.getAllByLabelText('More actions')[0]);
    await user.click(screen.getByRole('menuitem', { name: /unpublish/i }));

    expect(updateReportVisibility).toHaveBeenCalledWith('r1', 'private');
    expect(
      await screen.findByRole('dialog', { name: 'Could not unpublish report' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Report must be unpinned and removed from the dashboard before it can be made private',
      ),
    ).toBeInTheDocument();
  });

  it('shows the delete failure in the confirmation dialog', async () => {
    deleteReport.mockRejectedValue(
      new Error('Report must be unpinned before it can be deleted'),
    );
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(screen.getAllByLabelText('More actions')[0]);
    await user.click(screen.getByRole('menuitem', { name: /delete/i }));
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    expect(deleteReport).toHaveBeenCalledWith('r1');
    expect(
      await screen.findByRole('dialog', { name: 'Delete report?' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Report must be unpinned before it can be deleted'),
    ).toBeInTheDocument();
  });

  it('shows the space column and links to the space', () => {
    mockUseReportsList.mockReturnValue({
      reports: [{ ...REPORTS[0], space_id: 'sp1' }, REPORTS[1]],
      total: 2,
      page: 1,
      perPage: 500,
      loading: false,
      error: null,
      refresh: refreshReports,
    });

    render(<ReportsList />, { wrapper: Wrapper });

    // The Space column is hideBelow="md", so at the test viewport it is in the
    // DOM but display:none — hence getByText / hidden:true rather than a role
    // query, matching how the other responsive columns are asserted above.
    expect(screen.getByText('Space')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Cloud Security', hidden: true }),
    ).toHaveAttribute('href', '/app/spaces/sp1');
  });

  it('moves a report into a space and sub-space', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(screen.getAllByLabelText('More actions')[0]);
    await user.click(screen.getByRole('menuitem', { name: /move to space/i }));

    await user.click(screen.getByRole('combobox', { name: 'Space' }));
    await user.click(screen.getByRole('option', { name: 'Cloud Security' }));
    await user.click(screen.getByRole('combobox', { name: 'Sub-space' }));
    await user.click(screen.getByRole('option', { name: 'Network' }));
    await user.click(screen.getByRole('button', { name: 'Move' }));

    await waitFor(() => {
      expect(setReportSpace).toHaveBeenCalledWith('r1', 'sp1', 'ss1');
    });
  });

  it('disables the sub-space select until a space is chosen', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(screen.getAllByLabelText('More actions')[0]);
    await user.click(screen.getByRole('menuitem', { name: /move to space/i }));

    // Mirrors the API rule, so the user cannot build a request that 400s.
    expect(screen.getByRole('combobox', { name: 'Sub-space' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('resets the sub-space when the space changes', async () => {
    mockUseReportsList.mockReturnValue({
      reports: [{ ...REPORTS[0], space_id: 'sp1', subspace_id: 'ss1' }],
      total: 1,
      page: 1,
      perPage: 500,
      loading: false,
      error: null,
      refresh: refreshReports,
    });
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(screen.getAllByLabelText('More actions')[0]);
    await user.click(screen.getByRole('menuitem', { name: /move to space/i }));
    await user.click(screen.getByRole('combobox', { name: 'Space' }));
    await user.click(screen.getByRole('option', { name: 'None' }));
    await user.click(screen.getByRole('button', { name: 'Move' }));

    await waitFor(() => {
      expect(setReportSpace).toHaveBeenCalledWith('r1', null, null);
    });
  });

  // -------------------------------------------------------------------------
  // Bulk actions
  // -------------------------------------------------------------------------

  it('shows no selection bar until a row is selected', () => {
    render(<ReportsList />, { wrapper: Wrapper });

    expect(screen.queryByText(/selected$/)).toBeNull();
    expect(
      screen.queryByRole('button', { name: 'Move to space' }),
    ).not.toBeInTheDocument();
  });

  it('reveals bulk actions once rows are selected', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(
      screen.getAllByRole('checkbox', { name: 'Select row' })[0],
    );

    expect(screen.getByText('1 selected')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Move to space' }),
    ).toBeInTheDocument();
  });

  it('selects every row on the page from the header checkbox', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(
      screen.getByRole('checkbox', { name: 'Select all rows on this page' }),
    );

    expect(screen.getByText('2 selected')).toBeInTheDocument();
  });

  it('bulk pins every selected report', async () => {
    const pinReport = jest.fn().mockResolvedValue(undefined);
    mockUseReportsMutations.mockReturnValue({
      createReport: jest.fn(),
      cloneReport,
      saveReportVersion: jest.fn(),
      setDashboardReport: jest.fn(),
      pinReport,
      updateReportVisibility,
      deleteReport,
      setReportSpace,
    });
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(
      screen.getByRole('checkbox', { name: 'Select all rows on this page' }),
    );
    await user.click(screen.getByRole('button', { name: 'Pin' }));

    await waitFor(() => {
      expect(pinReport).toHaveBeenCalledTimes(2);
    });
    expect(pinReport).toHaveBeenCalledWith('r1', true);
    expect(pinReport).toHaveBeenCalledWith('r2', true);
  });

  it('bulk moves every selected report into the chosen space', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(
      screen.getByRole('checkbox', { name: 'Select all rows on this page' }),
    );
    await user.click(screen.getByRole('button', { name: 'Move to space' }));
    await user.click(screen.getByRole('combobox', { name: 'Space' }));
    await user.click(screen.getByRole('option', { name: 'Cloud Security' }));
    await user.click(screen.getByRole('button', { name: 'Move' }));

    await waitFor(() => {
      expect(setReportSpace).toHaveBeenCalledTimes(2);
    });
    expect(setReportSpace).toHaveBeenCalledWith('r1', 'sp1', null);
    expect(setReportSpace).toHaveBeenCalledWith('r2', 'sp1', null);
  });

  it('reports per-report failures without aborting the rest of the batch', async () => {
    setReportSpace
      .mockRejectedValueOnce(new Error('nope'))
      .mockResolvedValueOnce(REPORTS[1]);
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(
      screen.getByRole('checkbox', { name: 'Select all rows on this page' }),
    );
    await user.click(screen.getByRole('button', { name: 'Move to space' }));
    await user.click(screen.getByRole('combobox', { name: 'Space' }));
    await user.click(screen.getByRole('option', { name: 'Cloud Security' }));
    await user.click(screen.getByRole('button', { name: 'Move' }));

    // Both were attempted, and the failure is surfaced by name.
    await waitFor(() => {
      expect(setReportSpace).toHaveBeenCalledTimes(2);
    });
    expect(await screen.findByText(/Executive Risk: nope/)).toBeInTheDocument();
  });

  it('hides the checkbox column without reports:write', () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => false,
      loading: false,
      currentUser: null,
    });

    render(<ReportsList />, { wrapper: Wrapper });

    expect(screen.queryByRole('checkbox', { name: 'Select row' })).toBeNull();
  });

  // -------------------------------------------------------------------------
  // Publish gating
  // -------------------------------------------------------------------------

  it('offers Publish on a private report owned by the user', async () => {
    mockUseReportsList.mockReturnValue({
      reports: [{ ...REPORTS[1], created_by: 'alice' }],
      total: 1,
      page: 1,
      perPage: 500,
      loading: false,
      error: null,
      refresh: refreshReports,
    });
    const user = userEvent.setup({ delay: null });
    render(<ReportsList />, { wrapper: Wrapper });

    await user.click(screen.getAllByLabelText('More actions')[0]);

    expect(
      screen.getByRole('menuitem', { name: /publish/i }),
    ).not.toHaveAttribute('aria-disabled', 'true');
  });
});
