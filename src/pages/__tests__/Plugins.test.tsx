import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Plugins from 'src/pages/Plugins';
import * as pluginsApi from 'src/hooks/usePluginsApi';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));
jest.mock('src/hooks/usePluginsApi', () => ({
  usePluginsList: jest.fn(),
  usePluginMutations: jest.fn(),
}));
jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const usePermissions = permissionsModule.usePermissions as jest.Mock;
const usePluginsList = pluginsApi.usePluginsList as jest.Mock;
const usePluginMutations = pluginsApi.usePluginMutations as jest.Mock;
const theme = createTheme();

const PLUGIN: pluginsApi.PluginListItem = {
  plugin_id: 'security_review',
  name: 'security-review',
  package_version: '1.0.0',
  description: 'Review repositories',
  enabled: true,
  current_revision: 2,
  package_digest: 'a'.repeat(64),
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  created_by: 'alice',
  updated_by: 'bob',
  diagnostics: [],
};

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/plugins']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route path="/app/plugins" element={<>{children}</>} />
          <Route
            path="/app/plugins/:pluginId/edit"
            element={<div>Plugin editor</div>}
          />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('Plugins', () => {
  const create = jest.fn();
  const createDraft = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    usePermissions.mockReturnValue((permission: string) =>
      permission.startsWith('plugins:'),
    );
    usePluginsList.mockReturnValue({
      plugins: [PLUGIN],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    create.mockResolvedValue({ ...PLUGIN, plugin_id: 'repository_review' });
    createDraft.mockResolvedValue(undefined);
    usePluginMutations.mockReturnValue({
      create,
      install: jest.fn(),
      createDraft,
      setEnabled: jest.fn(),
      remove: jest.fn(),
    });
  });

  afterEach(cleanup);

  it('uses the standard list table and overflow actions', () => {
    render(<Plugins />, { wrapper: Wrapper });

    expect(
      screen.getByRole('heading', { level: 1, name: 'Agent Plugins' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('columnheader', { name: 'Namespace' }),
    ).toBeInTheDocument();
    expect(screen.getByText('security-review')).toBeInTheDocument();
    expect(screen.getByText('security_review')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'New plugin' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Install ZIP' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(screen.getByRole('menuitem', { name: 'Edit' })).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: 'Disable' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: 'Delete' }),
    ).toBeInTheDocument();
  });

  it('creates a plugin from structured fields and opens its draft', async () => {
    render(<Plugins />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole('button', { name: 'New plugin' }));
    const dialog = screen.getByRole('dialog', { name: 'New Agent Plugin' });
    fireEvent.change(
      within(dialog).getByRole('textbox', { name: /namespace/i }),
      {
        target: { value: 'repository_review' },
      },
    );
    fireEvent.change(
      within(dialog).getByRole('textbox', { name: /package name/i }),
      {
        target: { value: 'repository-review' },
      },
    );
    fireEvent.change(
      within(dialog).getByRole('textbox', { name: /description/i }),
      {
        target: { value: 'Review repositories' },
      },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith({
        plugin_id: 'repository_review',
        name: 'repository-review',
        version: '1.0.0',
        description: 'Review repositories',
      });
    });
    await waitFor(() =>
      expect(createDraft).toHaveBeenCalledWith('repository_review'),
    );
    await waitFor(() =>
      expect(screen.getByText('Plugin editor')).toBeInTheDocument(),
    );
  });
});
