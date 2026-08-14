import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ToolsetTools from 'src/pages/ToolsetTools';
import * as toolsetsApiModule from 'src/hooks/useToolsetsApi';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/hooks/useToolsetsApi', () => ({
  useToolsList: jest.fn(),
  useToolMutations: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;
const mockUseToolsList = toolsetsApiModule.useToolsList as unknown as jest.Mock;
const mockUseToolMutations =
  toolsetsApiModule.useToolMutations as unknown as jest.Mock;
const theme = createTheme();

const TOOL: toolsetsApiModule.ToolItem = {
  tool_id: 'recent_cves',
  toolset_id: 'security_tools',
  name: 'Recent CVEs',
  description: 'Find recent CVEs',
  cypher: 'MATCH (c:CVE) RETURN c LIMIT 10',
  parameters: [
    {
      name: 'limit',
      type: 'integer',
      description: 'Limit',
      required: false,
      default: 10,
    },
  ],
  enabled: true,
  current_version: 3,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  created_by: 'alice',
  updated_by: 'bob',
  effective_enabled: true,
  disabled_reason: null,
};

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/toolsets/security_tools/tools']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route
            path="/app/toolsets/:toolsetId/tools"
            element={<>{children}</>}
          />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

function ExternalWrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/toolsets/__external_github__/tools']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route
            path="/app/toolsets/:toolsetId/tools"
            element={<>{children}</>}
          />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('ToolsetTools', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUsePermissions.mockReturnValue((permission: string) =>
      permission.startsWith('tools:'),
    );
    mockUseToolsList.mockReturnValue({
      tools: [TOOL],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseToolMutations.mockReturnValue({
      createTool: jest.fn(),
      updateTool: jest.fn(),
      deleteTool: jest.fn(),
    });
  });

  afterEach(cleanup);

  it('renders tool update metadata and parameter count columns consistently', () => {
    render(<ToolsetTools />, { wrapper: Wrapper });

    expect(screen.getByText('Parameters')).toBeInTheDocument();
    expect(screen.getByText('Last updated')).toBeInTheDocument();
    expect(screen.getByText('Updated by')).toBeInTheDocument();
    expect(screen.getByText('Recent CVEs')).toBeInTheDocument();
    expect(screen.getByText('v3')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
  });

  it('renders external MCP tools as read-only', () => {
    mockUseToolsList.mockReturnValue({
      tools: [
        {
          ...TOOL,
          tool_id: '__external_ext__github__get_file_contents__',
          toolset_id: '__external_github__',
          name: 'get_file_contents',
          current_version: 0,
          created_at: '',
          updated_at: '',
          created_by: '',
          updated_by: null,
        },
      ],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });

    render(<ToolsetTools />, { wrapper: ExternalWrapper });

    expect(screen.getByText('External MCP')).toBeInTheDocument();
    expect(
      screen.getByText('ext__github__get_file_contents'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'New tool' })).toBeNull();
  });
});
