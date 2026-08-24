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
// Exhaustive: a module-factory mock replaces this module for every test file
// in the shared Bun process, so omitting an export breaks the other suites.
jest.mock('src/hooks/usePluginsApi', () => ({
  usePluginsList: jest.fn(),
  usePluginVersionsList: jest.fn(),
  usePluginContents: jest.fn(),
  usePluginMutations: jest.fn(),
}));
jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const usePermissions = permissionsModule.usePermissions as jest.Mock;
const usePluginsList = pluginsApi.usePluginsList as jest.Mock;
const usePluginContents = pluginsApi.usePluginContents as jest.Mock;
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
    usePluginContents.mockReturnValue({
      skills: [
        {
          plugin_id: 'security_review',
          skill_id: 'review_repo',
          portable_name: 'review-repo',
          title: 'Review repository',
          description: 'Review a repository for security issues',
          template: 'Review.',
          parameters: [],
          triggers: [],
          allowed_tools: ['graph__query'],
          enabled: true,
          source_path: 'skills/review-repo',
          aliases: [],
          revision: 2,
          package_digest: 'a'.repeat(64),
          has_scripts: false,
        },
      ],
      files: [
        {
          path: 'skills/review-repo/SKILL.md',
          media_type: 'text/markdown',
          size: 400,
          sha256: 'b'.repeat(64),
          executable: false,
          etag: '"b"',
        },
      ],
      loading: false,
      error: null,
    });
    usePluginMutations.mockReturnValue({
      create,
      install: jest.fn(),
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
      screen.getByRole('columnheader', { name: 'Status' }),
    ).toBeInTheDocument();
    expect(screen.getByText('security-review')).toBeInTheDocument();
    // The name opens the read-only detail dialog; editing stays a row action.
    fireEvent.click(screen.getByText('security-review'));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('security_review');
    // The detail view carries the package's skills and their structure.
    expect(dialog).toHaveTextContent('security_review__review_repo');
    expect(dialog).toHaveTextContent('Review a repository for security issues');
    expect(dialog).toHaveTextContent('graph__query');
    expect(dialog).toHaveTextContent('SKILL.md');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
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
      screen.getByRole('menuitem', { name: 'View history' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: 'Delete' }),
    ).toBeInTheDocument();
  });

  it('creates a plugin from structured fields and opens its editor', async () => {
    render(<Plugins />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole('button', { name: 'New plugin' }));
    const dialog = screen.getByRole('dialog', { name: 'New Agent Plugin' });
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
      // No namespace is supplied: the server derives it from the name.
      expect(create).toHaveBeenCalledWith({
        name: 'repository-review',
        version: '1.0.0',
        description: 'Review repositories',
      });
    });
    await waitFor(() =>
      expect(screen.getByText('Plugin editor')).toBeInTheDocument(),
    );
  });

  it('names the diagnostics on hover instead of only counting them', async () => {
    usePluginsList.mockReturnValue({
      plugins: [
        {
          ...PLUGIN,
          diagnostics: [
            {
              severity: 'warning',
              code: 'unchanged_package_version',
              message: 'Package contents changed but version is still 1.0.0.',
              path: 'plugin.json',
              skill: null,
            },
          ],
        },
      ],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    render(<Plugins />, { wrapper: Wrapper });

    fireEvent.mouseOver(screen.getByText('1'));
    expect(
      await screen.findByText(/Package contents changed but version is still/),
    ).toBeInTheDocument();
  });
});
