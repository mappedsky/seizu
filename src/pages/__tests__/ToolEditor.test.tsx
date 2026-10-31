import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ToolEditor from 'src/pages/ToolEditor';
import * as cypherQueryModule from 'src/hooks/useCypherQuery';
import * as permissionsModule from 'src/hooks/usePermissions';
import * as toolsetsApi from 'src/hooks/useToolsetsApi';

const TOOL: toolsetsApi.ToolItem = {
  tool_id: 'recent_cves',
  toolset_id: 'security_tools',
  name: 'Recent CVEs',
  description: 'Find recent CVEs',
  cypher: 'MATCH (c:CVE) RETURN c LIMIT $limit',
  parameters: [
    {
      name: 'limit',
      type: 'integer',
      description: 'How many',
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

const IDLE: cypherQueryModule.QueryState = {
  loading: false,
  error: null,
  records: undefined,
  first: undefined,
  warnings: [],
  queryErrors: [],
  tokenExpired: false,
  historyId: null,
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/app/toolsets/:toolsetId/tools"
          element={<div>tool list</div>}
        />
        <Route
          path="/app/toolsets/:toolsetId/tools/new"
          element={<ToolEditor />}
        />
        <Route
          path="/app/toolsets/:toolsetId/tools/:toolId/edit"
          element={<ToolEditor />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ToolEditor', () => {
  const createTool = jest.fn();
  const updateTool = jest.fn();
  const runDraft = jest.fn();
  let queryState: cypherQueryModule.QueryState = IDLE;
  let spies: jest.SpyInstance[] = [];

  beforeEach(() => {
    jest.clearAllMocks();
    createTool.mockResolvedValue(TOOL);
    updateTool.mockResolvedValue(TOOL);
    queryState = IDLE;
    spies = [
      jest.spyOn(permissionsModule, 'usePermissionState').mockReturnValue({
        hasPermission: () => true,
        loading: false,
        currentUser: null,
      }),
      jest.spyOn(toolsetsApi, 'useToolsList').mockReturnValue({
        tools: [TOOL],
        loading: false,
        error: null,
        refresh: jest.fn(),
      }),
      jest.spyOn(toolsetsApi, 'useToolMutations').mockReturnValue({
        createTool,
        updateTool,
        deleteTool: jest.fn(),
      }),
      jest
        .spyOn(cypherQueryModule, 'useLazyCypherQuery')
        .mockImplementation(() => [runDraft, queryState]),
    ];
  });

  afterEach(() => {
    cleanup();
    spies.forEach((spy) => spy.mockRestore());
  });

  it('declares a parameter the query uses and creates the tool', async () => {
    renderAt('/app/toolsets/security_tools/tools/new');

    const save = screen.getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();
    expect(
      screen.getByText(
        'Needs an ID, a name and a query before it can be saved.',
      ),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'ID' }), {
      target: { value: 'by_severity' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
      target: { value: 'CVEs by severity' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Cypher query' }), {
      target: { value: 'MATCH (c:CVE {severity: $severity}) RETURN c' },
    });

    expect(screen.getByTestId('tool-signature')).toHaveTextContent(
      'security_tools__by_severity()',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Declare $severity' }));
    expect(screen.getByTestId('tool-signature')).toHaveTextContent(
      'security_tools__by_severity(severity: string)',
    );
    expect(screen.queryByRole('button', { name: 'Declare $severity' })).toBe(
      null,
    );

    fireEvent.click(save);

    await waitFor(() => expect(screen.getByText('tool list')).toBeTruthy());
    expect(createTool).toHaveBeenCalledWith({
      tool_id: 'by_severity',
      name: 'CVEs by severity',
      description: '',
      cypher: 'MATCH (c:CVE {severity: $severity}) RETURN c',
      parameters: [
        {
          name: 'severity',
          type: 'string',
          description: '',
          required: true,
          default: null,
        },
      ],
      enabled: true,
    });
  });

  it('rejects an ID that is not lower_snake_case', () => {
    renderAt('/app/toolsets/security_tools/tools/new');

    fireEvent.change(screen.getByRole('textbox', { name: 'ID' }), {
      target: { value: 'BadId' },
    });

    expect(
      screen.getByText('Use lower_snake_case, starting with a letter'),
    ).toBeInTheDocument();
  });

  it('edits an existing tool with a version comment', async () => {
    renderAt('/app/toolsets/security_tools/tools/recent_cves/edit');

    expect(
      screen.getByRole('heading', { name: 'Edit Recent CVEs' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Current version v3.')).toBeInTheDocument();
    expect(screen.getByTestId('tool-signature')).toHaveTextContent(
      'security_tools__recent_cves(limit?: integer = 10)',
    );

    fireEvent.change(screen.getByRole('textbox', { name: 'Cypher query' }), {
      target: { value: 'MATCH (c:CVE) RETURN c' },
    });
    expect(screen.getByText('No $limit in the query')).toBeInTheDocument();
    expect(
      screen.getByText('Unsaved changes. Saving creates v4.'),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'Version comment' }), {
      target: { value: 'Drop the limit' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(updateTool).toHaveBeenCalled());
    expect(updateTool).toHaveBeenCalledWith(
      'recent_cves',
      expect.objectContaining({
        cypher: 'MATCH (c:CVE) RETURN c',
        comment: 'Drop the limit',
      }),
    );
  });

  it('runs the draft with defaults and typed arguments', () => {
    queryState = { ...IDLE, records: [{ c: 'CVE-1' }], first: { c: 'CVE-1' } };
    renderAt('/app/toolsets/security_tools/tools/recent_cves/edit');

    fireEvent.click(screen.getByRole('button', { name: 'Run draft' }));
    expect(runDraft).toHaveBeenLastCalledWith({ limit: 10 });
    expect(screen.getByText('1 row')).toBeInTheDocument();
    expect(screen.getByTestId('try-it-results')).toHaveTextContent('CVE-1');

    fireEvent.change(screen.getByRole('textbox', { name: 'limit' }), {
      target: { value: '5' },
    });
    expect(screen.getByText('from an earlier draft')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Run draft' }));
    expect(runDraft).toHaveBeenLastCalledWith({ limit: 5 });
  });

  it('does not run with an argument of the wrong type', () => {
    renderAt('/app/toolsets/security_tools/tools/recent_cves/edit');

    fireEvent.change(screen.getByRole('textbox', { name: 'limit' }), {
      target: { value: 'many' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run draft' }));

    expect(runDraft).not.toHaveBeenCalled();
    expect(screen.getByText('Enter a whole number')).toBeInTheDocument();
  });

  it('asks before discarding unsaved edits', () => {
    renderAt('/app/toolsets/security_tools/tools/recent_cves/edit');

    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
      target: { value: 'Renamed' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));

    expect(screen.getByText('Discard unsaved changes?')).toBeInTheDocument();
  });

  it('refuses to edit a built-in toolset', () => {
    renderAt('/app/toolsets/__builtin_graph__/tools/new');

    expect(
      screen.getByText('Built-in and external MCP tools cannot be edited.'),
    ).toBeInTheDocument();
  });
});
