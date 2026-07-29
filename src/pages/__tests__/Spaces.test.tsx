import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import Spaces from 'src/pages/Spaces';
import * as spacesApiModule from 'src/hooks/useSpacesApi';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/hooks/useSpacesApi', () => ({
  useSpacesList: jest.fn(),
  useSpaceMutations: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;
const mockUseSpacesList = spacesApiModule.useSpacesList as unknown as jest.Mock;
const mockUseSpaceMutations =
  spacesApiModule.useSpaceMutations as unknown as jest.Mock;
const theme = createTheme();

const SPACES: spacesApiModule.SpaceListItem[] = [
  {
    space_id: 'sp1',
    name: 'Cloud Security',
    description: 'AWS and GCP posture',
    overview_report_id: 'ovr1',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    created_by: 'alice',
    updated_by: 'bob',
  },
];

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/spaces']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route path="/app/spaces" element={children} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('Spaces', () => {
  let createSpace: jest.Mock;
  let updateSpace: jest.Mock;
  let deleteSpace: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    createSpace = jest
      .fn()
      .mockResolvedValue({ ...SPACES[0], space_id: 'sp2' });
    updateSpace = jest.fn().mockResolvedValue(SPACES[0]);
    deleteSpace = jest.fn().mockResolvedValue(undefined);
    mockUsePermissions.mockReturnValue(() => true);
    mockUseSpacesList.mockReturnValue({
      spaces: SPACES,
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseSpaceMutations.mockReturnValue({
      createSpace,
      updateSpace,
      deleteSpace,
    });
  });

  afterEach(cleanup);

  it('renders the space list', () => {
    render(<Spaces />, { wrapper: Wrapper });

    expect(screen.getByText('Cloud Security')).toBeInTheDocument();
    expect(screen.getByText('AWS and GCP posture')).toBeInTheDocument();
  });

  it('shows the empty state when there are no spaces', () => {
    mockUseSpacesList.mockReturnValue({
      spaces: [],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });

    render(<Spaces />, { wrapper: Wrapper });

    expect(
      screen.getByText('No spaces yet. Create one above.'),
    ).toBeInTheDocument();
  });

  it('hides the create button without spaces:write', () => {
    mockUsePermissions.mockReturnValue(
      (permission: string) => permission !== 'spaces:write',
    );

    render(<Spaces />, { wrapper: Wrapper });

    expect(
      screen.queryByRole('button', { name: /new space/i }),
    ).not.toBeInTheDocument();
  });

  it('creates a space through the dialog', async () => {
    render(<Spaces />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: /new space/i }));
    fireEvent.change(screen.getByRole('textbox', { name: /^name/i }), {
      target: { value: 'Identity' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(createSpace).toHaveBeenCalledWith('Identity', '');
    });
  });

  it('surfaces the 409 detail when deleting a non-empty space', async () => {
    deleteSpace.mockRejectedValue(
      new Error(
        'Move every report out of the space and delete its sub-spaces before deleting it',
      ),
    );

    render(<Spaces />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(
        screen.getByText(/Move every report out of the space/),
      ).toBeInTheDocument();
    });
  });
});
