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
import PluginEditor from 'src/pages/PluginEditor';
import * as pluginsApi from 'src/hooks/usePluginsApi';
import * as toolsetsApi from 'src/hooks/useToolsetsApi';
import { PLUGIN_SCHEMA } from 'src/pluginAuthoring';

// Exhaustive: a module-factory mock replaces this module for every test file
// in the shared Bun process, so omitting an export breaks the other suites.
jest.mock('src/hooks/usePluginsApi', () => ({
  usePluginsList: jest.fn(),
  usePluginVersionsList: jest.fn(),
  usePluginMutations: jest.fn(),
}));
jest.mock('src/hooks/useToolsetsApi', () => ({
  useToolCatalog: jest.fn(),
}));
const usePluginMutations = pluginsApi.usePluginMutations as jest.Mock;
const useToolCatalog = toolsetsApi.useToolCatalog as jest.Mock;
const theme = createTheme();
const encoder = new TextEncoder();

const manifest = {
  $schema: PLUGIN_SCHEMA,
  name: 'security-review',
  version: '1.0.0',
  description: 'Security review skills',
  extensions: {
    'com.mappedsky.seizu': {
      skillsetId: 'security_review',
      skills: {
        'review-repository': {
          skillId: 'review_repository',
          title: 'Review repository',
          enabled: true,
          triggers: ['review repository'],
          parameters: [],
          aliases: [],
        },
      },
    },
  },
};

const file = (path: string): pluginsApi.PluginFileInfo => ({
  path,
  media_type: path.endsWith('.json') ? 'application/json' : 'text/markdown',
  size: 20,
  sha256: path,
  executable: false,
  etag: `"${path}"`,
});

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/plugins/security_review/edit']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route path="/app/plugins/:pluginId/edit" element={<>{children}</>} />
          <Route path="/app/plugins" element={<div>Plugins list</div>} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('PluginEditor', () => {
  const validatePackage = jest.fn();
  const publishPackage = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    const files = [
      file('plugin.json'),
      file('skills/review-repository/SKILL.md'),
      file('skills/review-repository/references/checklist.md'),
    ];
    publishPackage.mockResolvedValue({});
    validatePackage.mockResolvedValue({ valid: true, diagnostics: [] });
    usePluginMutations.mockReturnValue({
      get: jest.fn().mockResolvedValue({
        plugin_id: 'security_review',
        current_revision: 4,
      }),
      listFiles: jest.fn().mockResolvedValue(files),
      readFile: jest
        .fn()
        .mockImplementation((_: string, __: number, path: string) => {
          const text =
            path === 'plugin.json'
              ? JSON.stringify(manifest)
              : '---\nname: review-repository\ndescription: Review a repository\nallowed-tools: graph__query\n---\nReview the repository.';
          return Promise.resolve({ bytes: encoder.encode(text) });
        }),
      validatePackage,
      publishPackage,
    });
    useToolCatalog.mockReturnValue({
      tools: [
        {
          mcp_name: 'graph__query',
          toolset_id: 'graph',
          tool_id: 'query',
          toolset_name: 'Graph',
          name: 'Query',
          enabled: true,
        },
      ],
      loading: false,
      error: null,
    });
  });

  afterEach(cleanup);

  it('renders structured manifest and skill authoring controls', async () => {
    render(<PluginEditor />, { wrapper: Wrapper });

    expect(
      await screen.findByRole('heading', { name: 'Plugin details' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /package name/i })).toHaveValue(
      'security-review',
    );
    expect(
      screen.getByRole('textbox', { name: /seizu namespace/i }),
    ).toHaveValue('security_review');
    expect(screen.queryByText('Plugin-relative path')).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Back to Agent Plugins' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText('Review repository'));

    expect(
      await screen.findByRole('textbox', { name: /skill id/i }),
    ).toHaveValue('review_repository');
    expect(screen.getByRole('textbox', { name: /description/i })).toHaveValue(
      'Review a repository',
    );
    fireEvent.click(screen.getByRole('button', { name: /Markdown source/i }));
    expect(screen.getByLabelText('SKILL.md instructions')).toHaveValue(
      'Review the repository.',
    );
    expect(
      screen.queryByText('Allowed tool declarations'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Choose tools' }));
    const allowedToolsDialog = screen.getByRole('dialog', {
      name: 'Allowed tools',
    });
    expect(within(allowedToolsDialog).getByText('Graph')).toBeInTheDocument();
    expect(
      within(allowedToolsDialog).getByRole('checkbox', {
        name: /Query graph__query/,
      }),
    ).toBeChecked();
    expect(
      within(allowedToolsDialog).getByRole('button', { name: 'Save' }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(allowedToolsDialog).getByRole('button', { name: 'Cancel' }),
    );
    expect(screen.getByText('graph__query')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Choose tools' }));
    const reopenedAllowedToolsDialog = screen.getByRole('dialog', {
      name: 'Allowed tools',
    });
    fireEvent.click(
      within(reopenedAllowedToolsDialog).getByRole('checkbox', {
        name: /Query graph__query/,
      }),
    );
    fireEvent.click(
      within(reopenedAllowedToolsDialog).getByRole('button', { name: 'Save' }),
    );
    expect(screen.getByText('No tools selected')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Add file' }));
    const supportingFileDialog = screen.getByRole('dialog', {
      name: 'Add supporting file',
    });
    expect(
      within(supportingFileDialog).getByRole('combobox', { name: 'Directory' }),
    ).toBeInTheDocument();
    expect(
      within(supportingFileDialog).getByRole('combobox', { name: 'Source' }),
    ).toBeInTheDocument();
    expect(screen.getByText('references/checklist.md')).toBeInTheDocument();
  });

  it('offers structured skill creation from the package navigation', async () => {
    render(<PluginEditor />, { wrapper: Wrapper });
    await screen.findByRole('heading', { name: 'Plugin details' });

    fireEvent.click(screen.getByRole('button', { name: 'Add skill' }));

    const dialog = screen.getByRole('dialog', { name: 'New skill' });
    expect(
      within(dialog).getByRole('textbox', { name: /portable name/i }),
    ).toBeInTheDocument();
    // The skill id is derived from the portable name, not authored (AGT-040).
    expect(
      within(dialog).queryByRole('textbox', { name: /skill id/i }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole('textbox', { name: /display title/i }),
    ).toBeInTheDocument();
  });

  it('publishes the whole package once, against the loaded revision', async () => {
    render(<PluginEditor />, { wrapper: Wrapper });
    await screen.findByRole('heading', { name: 'Plugin details' });

    // Nothing edited yet, so there is nothing to publish.
    expect(screen.getByRole('button', { name: /Publish/ })).toBeDisabled();

    fireEvent.change(screen.getByRole('textbox', { name: /package name/i }), {
      target: { value: 'security-review-2' },
    });
    const publish = screen.getByRole('button', { name: /Publish/ });
    expect(publish).toBeEnabled();
    fireEvent.click(publish);

    await waitFor(() => expect(publishPackage).toHaveBeenCalled());
    const [pluginId, files, baseRevision] = publishPackage.mock.calls[0];
    expect(pluginId).toBe('security_review');
    expect(baseRevision).toBe(4);
    // One request carrying every file: edited content inline, untouched
    // supporting files retained by digest.
    expect(files.map((file: { path: string }) => file.path).sort()).toEqual([
      'plugin.json',
      'skills/review-repository/SKILL.md',
      'skills/review-repository/references/checklist.md',
    ]);
    const retained = files.find((file: { path: string }) =>
      file.path.endsWith('checklist.md'),
    );
    expect(retained.sha256).toBe(
      'skills/review-repository/references/checklist.md',
    );
    expect(retained.content_base64).toBeUndefined();
  });

  it('warns before leaving with unpublished edits', async () => {
    render(<PluginEditor />, { wrapper: Wrapper });
    await screen.findByRole('heading', { name: 'Plugin details' });

    fireEvent.change(screen.getByRole('textbox', { name: /package name/i }), {
      target: { value: 'security-review-2' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Back to Agent Plugins' }),
    );
    expect(
      screen.getByRole('dialog', { name: 'Discard unpublished changes?' }),
    ).toBeInTheDocument();
  });

  it('leaves without a prompt when nothing was edited', async () => {
    render(<PluginEditor />, { wrapper: Wrapper });
    await screen.findByRole('heading', { name: 'Plugin details' });

    fireEvent.click(
      screen.getByRole('button', { name: 'Back to Agent Plugins' }),
    );
    expect(screen.getByText('Plugins list')).toBeInTheDocument();
  });
});
