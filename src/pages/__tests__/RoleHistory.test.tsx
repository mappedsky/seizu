import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import RoleHistory from 'src/pages/RoleHistory';
import * as rolesApiModule from 'src/hooks/useRolesApi';
import * as usePermissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));

jest.mock('src/hooks/useRolesApi', () => {
  return {
    isBuiltinRole: (roleId: string) => roleId.startsWith('builtin:'),
    useRoleVersionsList: jest.fn(),
    useRoleMutations: jest.fn(),
  };
});

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const mockUsePermissionState =
  usePermissionsModule.usePermissionState as jest.MockedFunction<
    typeof usePermissionsModule.usePermissionState
  >;
const mockUseRoleVersionsList =
  rolesApiModule.useRoleVersionsList as unknown as jest.Mock;
const mockUseRoleMutations =
  rolesApiModule.useRoleMutations as unknown as jest.Mock;
const theme = createTheme();

const VERSION_1: rolesApiModule.RoleVersion = {
  role_id: 'role1',
  name: 'Security Operator',
  description: 'Old permissions',
  permissions: ['reports:read'],
  version: 1,
  created_at: '2026-01-01T00:00:00Z',
  created_by: 'alice',
  comment: 'Initial version',
};

const VERSION_2: rolesApiModule.RoleVersion = {
  role_id: 'role1',
  name: 'Security Operator',
  description: 'Current permissions',
  permissions: ['reports:read', 'tools:call'],
  version: 2,
  created_at: '2026-01-02T00:00:00Z',
  created_by: 'bob',
  comment: null,
};

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
    <MemoryRouter initialEntries={['/app/roles/role1/history']}>
      <ThemeProvider theme={theme}>
        <TestLocation />
        <Routes>
          <Route path="/app/roles/:roleId/history" element={<>{children}</>} />
          <Route path="/app/roles" element={<div />} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

function setPermissions(permissions: string[]) {
  mockUsePermissionState.mockReturnValue({
    hasPermission: (permission: string) => permissions.includes(permission),
    loading: false,
    currentUser: null,
  });
}

describe('RoleHistory', () => {
  const updateRole = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    setPermissions(['roles:read', 'roles:write']);
    mockUseRoleVersionsList.mockReturnValue({
      versions: [VERSION_1, VERSION_2],
      loading: false,
      error: null,
    });
    updateRole.mockResolvedValue({});
    mockUseRoleMutations.mockReturnValue({ updateRole });
  });

  afterEach(cleanup);

  it('disables restore without roles:write', () => {
    setPermissions(['roles:read']);
    render(<RoleHistory />, { wrapper: Wrapper });

    fireEvent.click(screen.getAllByRole('button', { name: 'More actions' })[1]);

    expect(screen.getByRole('menuitem', { name: /restore/i })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('restores a non-current version by saving a new role version', async () => {
    render(<RoleHistory />, { wrapper: Wrapper });

    fireEvent.click(screen.getAllByRole('button', { name: 'More actions' })[1]);
    fireEvent.click(screen.getByRole('menuitem', { name: /restore/i }));

    await waitFor(() => {
      expect(updateRole).toHaveBeenCalledWith('role1', {
        name: 'Security Operator',
        description: 'Old permissions',
        permissions: ['reports:read'],
        comment: 'Restored from version 1',
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId('nav-location')).toHaveTextContent(
        '/app/roles',
      );
    });
  });
});
