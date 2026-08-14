import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import Toolsets from 'src/pages/Toolsets';
import * as toolsetsApiModule from 'src/hooks/useToolsetsApi';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/hooks/useToolsetsApi', () => ({
  useToolsetsList: jest.fn(),
  useToolsetMutations: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;
const mockUseToolsetsList =
  toolsetsApiModule.useToolsetsList as unknown as jest.Mock;
const mockUseToolsetMutations =
  toolsetsApiModule.useToolsetMutations as unknown as jest.Mock;
const theme = createTheme();

const TOOLSETS: toolsetsApiModule.ToolsetListItem[] = [
  {
    toolset_id: '__builtin_graph__',
    name: 'Graph',
    description: 'Built-in graph tools',
    enabled: true,
    current_version: 0,
    created_at: '',
    updated_at: '',
    created_by: 'system',
    updated_by: null,
  },
  {
    toolset_id: '__external_github__',
    name: 'github',
    description: 'External GitHub MCP tools',
    enabled: true,
    current_version: 0,
    created_at: '',
    updated_at: '',
    created_by: '',
    updated_by: null,
  },
  {
    toolset_id: 'security_tools',
    name: 'Security Tools',
    description: 'Custom security tooling',
    enabled: true,
    current_version: 2,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    created_by: 'alice',
    updated_by: 'bob',
  },
];

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/toolsets']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route path="/app/toolsets" element={<>{children}</>} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('Toolsets', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUsePermissions.mockReturnValue((permission: string) =>
      permission.startsWith('toolsets:'),
    );
    mockUseToolsetsList.mockReturnValue({
      toolsets: TOOLSETS,
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseToolsetMutations.mockReturnValue({
      createToolset: jest.fn(),
      updateToolset: jest.fn(),
      deleteToolset: jest.fn(),
    });
  });

  afterEach(cleanup);

  it('renders toolset type and update metadata columns consistently', () => {
    render(<Toolsets />, { wrapper: Wrapper });

    expect(
      screen.getByRole('columnheader', { name: 'Type' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Last updated')).toBeInTheDocument();
    expect(screen.getByText('Updated by')).toBeInTheDocument();

    expect(screen.getByText('Graph')).toBeInTheDocument();
    expect(screen.getByText('Built-in')).toBeInTheDocument();
    expect(screen.getByText('github')).toBeInTheDocument();
    expect(screen.getByText('External MCP')).toBeInTheDocument();
    expect(screen.getByText('Security Tools')).toBeInTheDocument();
    expect(screen.getByText('User-defined')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
  });
});
