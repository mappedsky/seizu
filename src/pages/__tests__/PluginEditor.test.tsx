import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PluginEditor from 'src/pages/PluginEditor';
import * as pluginsApi from 'src/hooks/usePluginsApi';
import * as toolsetsApi from 'src/hooks/useToolsetsApi';
import { PLUGIN_SCHEMA } from 'src/pluginAuthoring';

jest.mock('src/hooks/usePluginsApi', () => ({
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
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('PluginEditor', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    const files = [
      file('plugin.json'),
      file('skills/review-repository/SKILL.md'),
      file('skills/review-repository/references/checklist.md'),
    ];
    usePluginMutations.mockReturnValue({
      listDraftFiles: jest.fn().mockResolvedValue(files),
      readDraftFile: jest.fn().mockImplementation((_: string, path: string) => {
        const text =
          path === 'plugin.json'
            ? JSON.stringify(manifest)
            : path.endsWith('SKILL.md')
              ? '---\nname: review-repository\ndescription: Review a repository\nallowed-tools: graph__query\n---\nReview the repository.'
              : 'Checklist';
        return Promise.resolve({ bytes: encoder.encode(text) });
      }),
      writeDraftFile: jest.fn(),
      deleteDraftFile: jest.fn(),
      validateDraft: jest.fn(),
      publishDraft: jest.fn(),
      discardDraft: jest.fn(),
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
      screen.queryByRole('button', { name: 'Agent Plugins' }),
    ).not.toBeInTheDocument();

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
    expect(
      within(dialog).getByRole('textbox', { name: /skill id/i }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('textbox', { name: /display title/i }),
    ).toBeInTheDocument();
  });
});
