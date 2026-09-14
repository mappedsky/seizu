import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import SkillsetHistory from 'src/pages/SkillsetHistory';
import * as skillsetsApiModule from 'src/hooks/useSkillsetsApi';
import * as usePermissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/hooks/useSkillsetsApi', () => ({
  useSkillsetVersionsList: jest.fn(),
  useSkillsetMutations: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const mockUsePermissions =
  usePermissionsModule.usePermissions as jest.MockedFunction<
    typeof usePermissionsModule.usePermissions
  >;
const mockUseSkillsetVersionsList =
  skillsetsApiModule.useSkillsetVersionsList as unknown as jest.Mock;
const mockUseSkillsetMutations =
  skillsetsApiModule.useSkillsetMutations as unknown as jest.Mock;
const theme = createTheme();

const VERSION_1: skillsetsApiModule.SkillsetVersion = {
  skillset_id: 'skillset1',
  name: 'Agent Skills',
  description: 'Older description',
  enabled: false,
  version: 1,
  created_at: '2026-01-01T00:00:00Z',
  created_by: 'alice',
  comment: 'Initial version',
};

const VERSION_2: skillsetsApiModule.SkillsetVersion = {
  skillset_id: 'skillset1',
  name: 'Agent Skills',
  description: 'Current description',
  enabled: true,
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
    <MemoryRouter initialEntries={['/app/skillsets/skillset1/history']}>
      <ThemeProvider theme={theme}>
        <TestLocation />
        <Routes>
          <Route
            path="/app/skillsets/:skillsetId/history"
            element={<>{children}</>}
          />
          <Route path="/app/skillsets" element={<div />} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('SkillsetHistory', () => {
  const updateSkillset = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    mockUsePermissions.mockReturnValue(
      (permission: string) => permission === 'skillsets:write',
    );
    mockUseSkillsetVersionsList.mockReturnValue({
      versions: [VERSION_1, VERSION_2],
      loading: false,
      error: null,
    });
    updateSkillset.mockResolvedValue({});
    mockUseSkillsetMutations.mockReturnValue({ updateSkillset });
  });

  afterEach(cleanup);

  it('disables restore for the current version', () => {
    render(<SkillsetHistory />, { wrapper: Wrapper });

    fireEvent.click(screen.getAllByRole('button', { name: 'More actions' })[0]);

    expect(screen.getByRole('menuitem', { name: /restore/i })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('opens version details from the version link', () => {
    render(<SkillsetHistory />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: 'v1' }));

    expect(
      screen.getByRole('dialog', { name: 'Agent Skills' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Older description')).toBeInTheDocument();
  });

  it('restores a non-current version by saving a new skillset version', async () => {
    render(<SkillsetHistory />, { wrapper: Wrapper });

    fireEvent.click(screen.getAllByRole('button', { name: 'More actions' })[1]);
    fireEvent.click(screen.getByRole('menuitem', { name: /restore/i }));

    await waitFor(() => {
      expect(updateSkillset).toHaveBeenCalledWith('skillset1', {
        name: 'Agent Skills',
        description: 'Older description',
        enabled: false,
        comment: 'Restored from version 1',
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId('nav-location')).toHaveTextContent(
        '/app/skillsets',
      );
    });
  });
});
