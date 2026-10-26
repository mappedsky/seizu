import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { AuthConfigContext } from 'src/authConfig.context';
import DashboardLayout from 'src/components/DashboardLayout';
import { DASHBOARD_SIDEBAR_COLLAPSED_STORAGE_KEY } from 'src/components/dashboardLayoutConstants';
import * as reportsApiModule from 'src/hooks/useReportsApi';
import * as useCurrentUserModule from 'src/hooks/useCurrentUser';
import * as usePermissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/hooks/useCurrentUser', () => ({
  useCurrentUser: jest.fn(),
}));

const theme = createTheme();
const mockUsePermissions =
  usePermissionsModule.usePermissions as jest.MockedFunction<
    typeof usePermissionsModule.usePermissions
  >;
const mockUseCurrentUser =
  useCurrentUserModule.useCurrentUser as jest.MockedFunction<
    typeof useCurrentUserModule.useCurrentUser
  >;

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={['/app/dashboard']}>
      <ThemeProvider theme={theme}>
        <AuthConfigContext.Provider
          value={{ auth_required: false, oidc: null, loaded: true }}
        >
          <Routes>
            <Route element={<DashboardLayout />} path="/app">
              <Route path="dashboard" element={<div>Dashboard content</div>} />
            </Route>
          </Routes>
        </AuthConfigContext.Provider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('DashboardLayout', () => {
  let useReportsList: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    mockUsePermissions.mockReturnValue(() => false);
    mockUseCurrentUser.mockReturnValue(null);
    useReportsList = jest.spyOn(
      reportsApiModule,
      'useReportsList',
    ) as unknown as jest.Mock;
    useReportsList.mockReturnValue({
      reports: [],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
  });

  afterEach(cleanup);

  it('offers a skip link to the main landmark and labels the navigation', () => {
    renderLayout();

    const skip = screen.getByRole('link', { name: 'Skip to content' });
    const main = screen.getByRole('main');
    expect(skip).toHaveAttribute('href', `#${main.id}`);
    expect(main).toHaveAttribute('tabindex', '-1');
    expect(
      screen.getAllByRole('navigation', { name: 'Main' }).length,
    ).toBeGreaterThan(0);
  });

  it('persists the collapsed sidebar state when toggled', () => {
    renderLayout();

    fireEvent.click(screen.getByRole('button', { name: /collapse sidebar/i }));

    expect(
      window.localStorage.getItem(DASHBOARD_SIDEBAR_COLLAPSED_STORAGE_KEY),
    ).toBe('true');
  });

  it('restores the collapsed sidebar state from localStorage', () => {
    window.localStorage.setItem(
      DASHBOARD_SIDEBAR_COLLAPSED_STORAGE_KEY,
      'true',
    );

    renderLayout();

    expect(
      screen.getByRole('button', { name: /expand sidebar/i }),
    ).toBeInTheDocument();
  });
});
