import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ReportHistory from 'src/pages/ReportHistory';
import ReportVersionView from 'src/pages/ReportVersionView';
import * as reportsApiModule from 'src/hooks/useReportsApi';
import * as usePermissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
  usePermissions: jest.fn(),
}));

const mockUsePermissionState =
  usePermissionsModule.usePermissionState as jest.MockedFunction<
    typeof usePermissionsModule.usePermissionState
  >;
const mockUsePermissions =
  usePermissionsModule.usePermissions as jest.MockedFunction<
    typeof usePermissionsModule.usePermissions
  >;

const theme = createTheme();

// Tracks the current location so tests can observe navigate() calls.
function TestLocation() {
  const { pathname } = useLocation();
  return (
    <div data-testid="nav-location" style={{ display: 'none' }}>
      {pathname}
    </div>
  );
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/reports/r1/history']}>
      <ThemeProvider theme={theme}>
        <TestLocation />
        <Routes>
          <Route path="/app/reports/:id/history" element={<>{children}</>} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

const VERSION_1 = {
  report_id: 'r1',
  name: 'My Report',
  version: 1,
  config: { rows: [] },
  created_at: '2024-01-01T00:00:00Z',
  created_by: 'alice@example.com',
  comment: 'Initial version',
  query_capabilities: {},
};

const VERSION_2 = {
  report_id: 'r1',
  name: 'My Report',
  version: 2,
  config: { rows: [] },
  created_at: '2024-01-02T00:00:00Z',
  created_by: 'bob@example.com',
  comment: null,
  query_capabilities: {},
};

describe('ReportHistory', () => {
  const mockSaveReportVersion = jest.fn();
  let useReportVersionsList: jest.Mock;
  let useReportVersion: jest.Mock;
  let useReportsMutations: jest.Mock;

  // Restore spies after all tests so other test files get the real module.
  afterAll(() => {
    jest.restoreAllMocks();
  });

  beforeEach(() => {
    jest.clearAllMocks();
    // Default: user has reports:write permission.
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: false,
      currentUser: null,
    });
    mockUsePermissions.mockReturnValue(() => true);
    useReportVersionsList = jest.spyOn(
      reportsApiModule,
      'useReportVersionsList',
    ) as unknown as jest.Mock;
    useReportVersionsList.mockReturnValue({
      versions: [VERSION_1, VERSION_2],
      loading: false,
      error: null,
    });
    useReportVersion = jest.spyOn(
      reportsApiModule,
      'useReportVersion',
    ) as unknown as jest.Mock;
    useReportVersion.mockReturnValue({
      reportVersion: VERSION_2,
      loading: false,
      error: null,
    });
    useReportsMutations = jest.spyOn(
      reportsApiModule,
      'useReportsMutations',
    ) as unknown as jest.Mock;
    useReportsMutations.mockReturnValue({
      saveReportVersion: mockSaveReportVersion,
    });
  });

  afterEach(cleanup);

  it('renders versions sorted newest-first', () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    const rows = screen.getAllByRole('row');
    // header + 2 data rows
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent('v2');
    expect(rows[2]).toHaveTextContent('v1');
  });

  it('labels the latest version as current', () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    const rows = screen.getAllByRole('row');
    expect(rows[1]).toHaveTextContent('current');
    expect(rows[2]).not.toHaveTextContent('current');
  });

  it('renders version numbers as links to the version view', () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    expect(screen.getByRole('link', { name: 'v2' })).toHaveAttribute(
      'href',
      '/app/reports/r1/versions/2',
    );
    expect(screen.getByRole('link', { name: 'v1' })).toHaveAttribute(
      'href',
      '/app/reports/r1/versions/1',
    );
  });

  it('passes breadcrumb state through the version link', async () => {
    function VersionWrapper({ children }: { children: React.ReactNode }) {
      return (
        <MemoryRouter initialEntries={['/app/reports/r1/history']}>
          <ThemeProvider theme={theme}>
            <TestLocation />
            <Routes>
              <Route
                path="/app/reports/:id/history"
                element={<>{children}</>}
              />
              <Route
                path="/app/reports/:id/versions/:version"
                element={<ReportVersionView />}
              />
            </Routes>
          </ThemeProvider>
        </MemoryRouter>
      );
    }

    render(<ReportHistory />, { wrapper: VersionWrapper });

    fireEvent.click(screen.getByRole('link', { name: 'v2' }));

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /back to history – my report/i }),
      ).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole('button', { name: /back to history – my report/i }),
    );

    await waitFor(() => {
      expect(screen.getByTestId('nav-location')).toHaveTextContent(
        '/app/reports/r1/history',
      );
    });
  });

  it('shows comment text in the row', () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    expect(screen.getByText('Initial version')).toBeInTheDocument();
  });

  it('shows em-dash for a null comment', () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    expect(screen.getAllByText('—')).toHaveLength(1);
  });

  it('shows the report name in the heading', () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      'Version history – My Report',
    );
  });

  it('shows back button with fromLabel when navigated from within the app', () => {
    function StatefulWrapper({ children }: { children: React.ReactNode }) {
      return (
        <MemoryRouter
          initialEntries={[
            { pathname: '/app/reports/r1', state: {} },
            {
              pathname: '/app/reports/r1/history',
              state: {
                fromLabel: 'My Report',
                originReturnTo: '/app/reports/r1',
              },
            },
          ]}
          initialIndex={1}
        >
          <ThemeProvider theme={theme}>
            <Routes>
              <Route path="/app/reports/:id" element={<div>report</div>} />
              <Route
                path="/app/reports/:id/history"
                element={<>{children}</>}
              />
            </Routes>
          </ThemeProvider>
        </MemoryRouter>
      );
    }

    render(<ReportHistory />, { wrapper: StatefulWrapper });

    expect(
      screen.getByRole('button', { name: /back to my report/i }),
    ).toBeInTheDocument();
  });

  it('returns to the original report path when the back button is clicked', async () => {
    function StatefulWrapper({ children }: { children: React.ReactNode }) {
      return (
        <MemoryRouter
          initialEntries={[
            { pathname: '/app/reports/r1', state: {} },
            {
              pathname: '/app/reports/r1/history',
              state: {
                fromLabel: 'My Report',
                originReturnTo: '/app/reports/r1',
              },
            },
          ]}
          initialIndex={1}
        >
          <ThemeProvider theme={theme}>
            <TestLocation />
            <Routes>
              <Route path="/app/reports/:id" element={<div>report</div>} />
              <Route
                path="/app/reports/:id/history"
                element={<>{children}</>}
              />
            </Routes>
          </ThemeProvider>
        </MemoryRouter>
      );
    }

    render(<ReportHistory />, { wrapper: StatefulWrapper });

    fireEvent.click(screen.getByRole('button', { name: /back to my report/i }));

    await waitFor(() => {
      expect(screen.getByTestId('nav-location')).toHaveTextContent(
        '/app/reports/r1',
      );
    });
  });

  it('hides back button when navigated directly (no fromLabel in state)', () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    expect(
      screen.queryByRole('button', { name: /back to/i }),
    ).not.toBeInTheDocument();
  });

  it('shows loading spinner while loading', () => {
    useReportVersionsList.mockReturnValue({
      versions: [],
      loading: true,
      error: null,
    });
    render(<ReportHistory />, { wrapper: Wrapper });

    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows error message when fetch fails', () => {
    useReportVersionsList.mockReturnValue({
      versions: [],
      loading: false,
      error: new Error('oops'),
    });
    render(<ReportHistory />, { wrapper: Wrapper });

    expect(
      screen.getByText('Failed to load version history'),
    ).toBeInTheDocument();
  });

  it('shows "No versions found" when versions list is empty', () => {
    useReportVersionsList.mockReturnValue({
      versions: [],
      loading: false,
      error: null,
    });
    render(<ReportHistory />, { wrapper: Wrapper });

    expect(screen.getByText('No versions found.')).toBeInTheDocument();
  });

  it('opens the per-row menu and navigates to version view on View click', async () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    // Open menu for the first data row (v2, the current version)
    const menuButtons = screen.getAllByRole('button', { name: 'More actions' });
    fireEvent.click(menuButtons[0]);

    fireEvent.click(screen.getByRole('menuitem', { name: /view/i }));

    await waitFor(() => {
      expect(screen.getByTestId('nav-location')).toHaveTextContent(
        '/app/reports/r1/versions/2',
      );
    });
  });

  it('Restore menu item is disabled for the current version', () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    // Open menu for the first data row (v2, the current/latest version)
    const menuButtons = screen.getAllByRole('button', { name: 'More actions' });
    fireEvent.click(menuButtons[0]);

    expect(screen.getByRole('menuitem', { name: /restore/i })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('Restore menu item is enabled for a non-current version', () => {
    render(<ReportHistory />, { wrapper: Wrapper });

    // Open menu for the second data row (v1, not current)
    const menuButtons = screen.getAllByRole('button', { name: 'More actions' });
    fireEvent.click(menuButtons[1]);

    expect(
      screen.getByRole('menuitem', { name: /restore/i }),
    ).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('Restore menu item is disabled for a non-current version when user lacks reports:write', () => {
    mockUsePermissions.mockReturnValue(() => false);
    render(<ReportHistory />, { wrapper: Wrapper });

    // Open menu for the second data row (v1, not current)
    const menuButtons = screen.getAllByRole('button', { name: 'More actions' });
    fireEvent.click(menuButtons[1]);

    expect(screen.getByRole('menuitem', { name: /restore/i })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('calls saveReportVersion and navigates when Restore is clicked', async () => {
    mockSaveReportVersion.mockResolvedValue({});
    render(<ReportHistory />, { wrapper: Wrapper });

    // Open menu for v1 row (not current)
    const menuButtons = screen.getAllByRole('button', { name: 'More actions' });
    fireEvent.click(menuButtons[1]);
    fireEvent.click(screen.getByRole('menuitem', { name: /restore/i }));

    await waitFor(() => {
      expect(mockSaveReportVersion).toHaveBeenCalledWith(
        'r1',
        VERSION_1.config,
        'Restored from version 1',
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId('nav-location')).toHaveTextContent(
        '/app/reports/r1',
      );
    });
  });
});
