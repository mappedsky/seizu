import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PluginHistory from 'src/pages/PluginHistory';
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
jest.mock('react-helmet', () => ({
  Helmet: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const usePermissions = permissionsModule.usePermissions as jest.Mock;
const usePluginVersionsList = pluginsApi.usePluginVersionsList as jest.Mock;
const usePluginContents = pluginsApi.usePluginContents as jest.Mock;
const usePluginMutations = pluginsApi.usePluginMutations as jest.Mock;
const theme = createTheme();

const VERSION_1: pluginsApi.PluginVersion = {
  plugin_id: 'security_review',
  revision: 1,
  manifest: { name: 'security-review', version: '1.0.0' },
  package_digest: 'a'.repeat(64),
  created_at: '2026-01-01T00:00:00Z',
  created_by: 'alice',
  comment: 'Installed package',
  diagnostics: [],
};

const VERSION_2: pluginsApi.PluginVersion = {
  ...VERSION_1,
  revision: 2,
  manifest: { name: 'security-review', version: '1.1.0' },
  created_by: 'bob',
  comment: 'Published draft',
};

const SKILL: pluginsApi.PluginSkillItem = {
  plugin_id: 'security_review',
  skill_id: 'review_repo',
  portable_name: 'review-repo',
  title: 'Review repository',
  description: 'Review a repository for security issues',
  template: 'Review {% $repo %}.',
  parameters: [{ name: 'repo', type: 'string', required: true }],
  triggers: ['review repo'],
  allowed_tools: ['graph__query'],
  enabled: true,
  source_path: 'skills/review-repo',
  aliases: [],
  revision: 1,
  package_digest: 'a'.repeat(64),
  has_scripts: true,
};

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/app/plugins/security_review/history']}>
        <Routes>
          <Route path="/app/plugins/:pluginId/history" element={children} />
          <Route path="/app/plugins" element={<div>Plugins list</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

describe('PluginHistory', () => {
  const restore = jest.fn();

  beforeEach(() => {
    usePermissions.mockReturnValue(() => true);
    usePluginVersionsList.mockReturnValue({
      versions: [VERSION_1, VERSION_2],
      loading: false,
      error: null,
    });
    restore.mockResolvedValue(undefined);
    usePluginMutations.mockReturnValue({ restore });
    usePluginContents.mockReturnValue({
      skills: [SKILL],
      files: [
        {
          path: 'plugin.json',
          media_type: 'application/json',
          size: 120,
          sha256: 'a'.repeat(64),
          executable: false,
          etag: '"a"',
        },
        {
          path: 'skills/review-repo/SKILL.md',
          media_type: 'text/markdown',
          size: 400,
          sha256: 'b'.repeat(64),
          executable: false,
          etag: '"b"',
        },
        {
          path: 'skills/review-repo/scripts/scan.sh',
          media_type: 'text/x-shellscript',
          size: 2048,
          sha256: 'c'.repeat(64),
          executable: true,
          etag: '"c"',
        },
      ],
      loading: false,
      error: null,
    });
  });

  afterEach(cleanup);

  it('lists revisions newest first and marks the current one', () => {
    render(<PluginHistory />, { wrapper: Wrapper });

    expect(
      screen.getByRole('heading', {
        level: 1,
        name: 'Version history – security_review',
      }),
    ).toBeInTheDocument();
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('v2');
    expect(rows[0]).toHaveTextContent('current');
    expect(rows[1]).toHaveTextContent('v1');
  });

  it('restores an older revision and disables restore on the current one', () => {
    render(<PluginHistory />, { wrapper: Wrapper });

    const rows = screen.getAllByRole('row').slice(1);
    fireEvent.click(
      within(rows[0]).getByRole('button', { name: 'More actions' }),
    );
    expect(screen.getByRole('menuitem', { name: 'Restore' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.keyDown(document.activeElement ?? document.body, {
      key: 'Escape',
    });

    fireEvent.click(
      within(rows[1]).getByRole('button', { name: 'More actions' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Restore' }));
    // The current revision rides along so a concurrent publish is refused
    // rather than reverted.
    expect(restore).toHaveBeenCalledWith(
      'security_review',
      1,
      2,
      'Restored from version 1',
    );
  });

  it('shows what an older revision contains before restoring it', () => {
    render(<PluginHistory />, { wrapper: Wrapper });

    const rows = screen.getAllByRole('row').slice(1);
    fireEvent.click(within(rows[1]).getByText('v1'));

    const dialog = screen.getByRole('dialog');
    // Warns that this is not the current revision, and shows the skill's
    // identity, description and file structure.
    expect(dialog).toHaveTextContent('This is an earlier revision');
    expect(dialog).toHaveTextContent('security_review__review_repo');
    expect(dialog).toHaveTextContent('Review a repository for security issues');
    expect(dialog).toHaveTextContent('skills/review-repo/ (portable name');
    expect(dialog).toHaveTextContent('scripts/scan.sh');
    expect(dialog).toHaveTextContent('plugin.json');
    expect(usePluginContents).toHaveBeenCalledWith('security_review', 1);
  });
});
