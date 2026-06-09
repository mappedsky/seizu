import {
  render,
  screen,
  fireEvent,
  cleanup,
  within,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import SkillsetSkills from 'src/pages/SkillsetSkills';
import * as skillsetsApiModule from 'src/hooks/useSkillsetsApi';
import * as toolsetsApiModule from 'src/hooks/useToolsetsApi';
import * as usePermissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const mockUsePermissions =
  usePermissionsModule.usePermissions as jest.MockedFunction<
    typeof usePermissionsModule.usePermissions
  >;
const theme = createTheme();

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/skillsets/responders/skills']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route
            path="/app/skillsets/:skillsetId/skills"
            element={<>{children}</>}
          />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('SkillsetSkills', () => {
  jest.setTimeout(20000);

  let useSkillsList: jest.Mock;
  let useSkillMutations: jest.Mock;
  let useToolCatalog: jest.Mock;
  let mockCreateSkill: jest.Mock;
  let mockUpdateSkill: jest.Mock;
  let mockRenderSkill: jest.Mock;

  afterAll(() => {
    jest.restoreAllMocks();
  });

  beforeEach(() => {
    jest.clearAllMocks();
    mockUsePermissions.mockReturnValue(() => true);
    useSkillsList = jest.spyOn(
      skillsetsApiModule,
      'useSkillsList',
    ) as unknown as jest.Mock;
    useSkillsList.mockReturnValue({
      skills: [
        {
          skill_id: 'triage',
          skillset_id: 'responders',
          name: 'Triage',
          description: 'Summarize an incident.',
          template: 'Summarize {{incident_id}}',
          enabled: true,
          current_version: 1,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          created_by: 'alice@example.com',
          updated_by: null,
        },
      ],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    useSkillMutations = jest.spyOn(
      skillsetsApiModule,
      'useSkillMutations',
    ) as unknown as jest.Mock;
    mockCreateSkill = jest.fn();
    mockUpdateSkill = jest.fn();
    mockRenderSkill = jest.fn().mockResolvedValue({ text: 'Rendered output' });
    useSkillMutations.mockReturnValue({
      createSkill: mockCreateSkill,
      updateSkill: mockUpdateSkill,
      deleteSkill: jest.fn(),
      renderSkill: mockRenderSkill,
    });
    useToolCatalog = jest.spyOn(
      toolsetsApiModule,
      'useToolCatalog',
    ) as unknown as jest.Mock;
    useToolCatalog.mockReturnValue({ tools: [], loading: false, error: null });
  });

  afterEach(cleanup);

  it('opens skill details when optional metadata arrays are absent', () => {
    render(<SkillsetSkills />, { wrapper: Wrapper });

    expect(screen.getByText('Last updated')).toBeInTheDocument();
    expect(screen.getByText('Updated by')).toBeInTheDocument();
    expect(screen.getByText('alice@example.com')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Triage'));

    const dialog = screen.getByRole('dialog', { name: 'Triage' });
    expect(dialog).toBeInTheDocument();
    // Slug shows the fully-qualified skillset__skill name.
    expect(within(dialog).getByText('responders__triage')).toBeInTheDocument();
    expect(within(dialog).getByText('No parameters.')).toBeInTheDocument();
  });

  it('renders a skill with structured parameter inputs', async () => {
    useSkillsList.mockReturnValue({
      skills: [
        {
          skill_id: 'triage',
          skillset_id: 'responders',
          name: 'Triage',
          description: 'Summarize an incident.',
          template: 'Summarize {{incident_id}} in {{count}} bullets',
          parameters: [
            {
              name: 'incident_id',
              type: 'string',
              description: 'Incident identifier',
              required: true,
              default: null,
            },
            {
              name: 'count',
              type: 'integer',
              description: 'Bullet count',
              required: false,
              default: 3,
            },
          ],
          enabled: true,
          current_version: 1,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          created_by: 'alice@example.com',
          updated_by: null,
        },
      ],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    render(<SkillsetSkills />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: /more actions/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /render/i }));

    const dialog = screen.getByRole('dialog', { name: 'Render skill' });
    expect(within(dialog).getByLabelText(/incident_id/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/count/)).toHaveValue(3);
    expect(within(dialog).getAllByText('Required')).toHaveLength(1);
    expect(within(dialog).getAllByText('Optional')).toHaveLength(1);

    fireEvent.change(within(dialog).getByLabelText(/incident_id/), {
      target: { value: 'INC-1' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: /render/i }));

    await waitFor(() =>
      expect(mockRenderSkill).toHaveBeenCalledWith('triage', {
        incident_id: 'INC-1',
        count: 3,
      }),
    );
    expect(
      await within(dialog).findByText('Rendered output'),
    ).toBeInTheDocument();
  });

  it('opens the new skill dialog with the markdown editor instead of a template textbox', () => {
    render(<SkillsetSkills />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: /new skill/i }));

    const dialog = screen.getByRole('dialog', { name: 'New Skill' });
    expect(
      within(dialog).getByRole('button', { name: /WYSIWYG editor/i }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(
      within(dialog).getByRole('button', { name: 'Bold' }),
    ).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('Template')).not.toBeInTheDocument();
  });

  it('switches an edited skill template to raw markdown source', () => {
    render(<SkillsetSkills />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: /more actions/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /edit/i }));

    const dialog = screen.getByRole('dialog', { name: 'Edit Skill' });
    fireEvent.click(
      within(dialog).getByRole('button', { name: /Markdown source/i }),
    );

    expect(within(dialog).getByLabelText('Template')).toHaveValue(
      'Summarize {{incident_id}}',
    );
  });

  it('warns before editing a skill with missing tool references and strips them on save', async () => {
    useSkillsList.mockReturnValue({
      skills: [
        {
          skill_id: 'triage',
          skillset_id: 'responders',
          name: 'Triage',
          description: 'Summarize an incident.',
          template: 'Summarize {{incident_id}}',
          parameters: [
            {
              name: 'incident_id',
              type: 'string',
              description: 'Incident identifier',
              required: true,
              default: null,
            },
          ],
          tools_required: ['graph__query', 'graph_tools__missing'],
          enabled: true,
          current_version: 1,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          created_by: 'alice@example.com',
          updated_by: null,
        },
      ],
      loading: false,
      error: null,
      refresh: jest.fn(),
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
    render(<SkillsetSkills />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: /more actions/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /edit/i }));

    const confirmDialog = screen.getByRole('dialog', {
      name: 'Remove missing tool references?',
    });
    expect(
      within(confirmDialog).getByText('graph_tools__missing'),
    ).toBeInTheDocument();

    fireEvent.click(
      within(confirmDialog).getByRole('button', { name: /continue editing/i }),
    );

    const editDialog = screen.getByRole('dialog', { name: 'Edit Skill' });
    fireEvent.click(within(editDialog).getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(mockUpdateSkill).toHaveBeenCalledWith(
        'triage',
        expect.objectContaining({
          tools_required: ['graph__query'],
        }),
      ),
    );
  });

  it('saves the raw markdown template string for a new skill', async () => {
    render(<SkillsetSkills />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: /new skill/i }));
    const dialog = screen.getByRole('dialog', { name: 'New Skill' });

    fireEvent.change(within(dialog).getByLabelText(/^ID/), {
      target: { value: 'respond_to_incident' },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Name/), {
      target: { value: 'Respond to incident' },
    });
    fireEvent.click(
      within(dialog).getByRole('button', { name: /Markdown source/i }),
    );
    fireEvent.change(within(dialog).getByLabelText('Template'), {
      target: { value: '## Incident\n\nSummarize {% $incident_id %}.' },
    });
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Add parameter' }),
    );
    fireEvent.change(within(dialog).getAllByLabelText(/^Name/)[1], {
      target: { value: 'incident_id' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(mockCreateSkill).toHaveBeenCalledWith(
        expect.objectContaining({
          skill_id: 'respond_to_incident',
          name: 'Respond to incident',
          template: '## Incident\n\nSummarize {% $incident_id %}.',
          parameters: [
            expect.objectContaining({ name: 'incident_id', type: 'string' }),
          ],
        }),
      ),
    );
    expect(mockUpdateSkill).not.toHaveBeenCalled();
  });

  it('blocks saving when a template placeholder is not declared as a parameter', () => {
    render(<SkillsetSkills />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: /new skill/i }));
    const dialog = screen.getByRole('dialog', { name: 'New Skill' });

    fireEvent.change(within(dialog).getByLabelText(/^ID/), {
      target: { value: 'bad_placeholder' },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Name/), {
      target: { value: 'Bad placeholder' },
    });
    fireEvent.click(
      within(dialog).getByRole('button', { name: /Markdown source/i }),
    );
    fireEvent.change(within(dialog).getByLabelText('Template'), {
      target: { value: 'Summarize {% $missing_param %}' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));

    expect(
      within(dialog).getByText(
        'Variable $missing_param must match a declared parameter.',
      ),
    ).toBeInTheDocument();
    expect(mockCreateSkill).not.toHaveBeenCalled();
  });
});
