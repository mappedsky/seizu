import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ModelProfileEditor from 'src/pages/ModelProfileEditor';
import * as permissionsModule from 'src/hooks/usePermissions';
import * as modelProfilesApi from 'src/hooks/useModelProfilesApi';

const profile: modelProfilesApi.ModelProfile = {
  profile_id: 'profile-1',
  name: 'Careful',
  description: 'A model profile',
  enabled: true,
  is_default: true,
  primary: { model_id: 'primary-model' },
  economy: { model_id: 'economy-model', reasoning_effort: 'low' },
  stage_overrides: { worker_summary: { reasoning_effort: 'minimal' } },
  user_reasoning_efforts: ['low', 'medium', 'high'],
  default_reasoning_effort: 'medium',
  run_cost_budget_usd: 2.5,
  current_version: 3,
  created_at: '2026-08-25T00:00:00Z',
  updated_at: '2026-08-26T00:00:00Z',
  created_by: 'admin',
  updated_by: 'admin',
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/model-profiles" element={<div>profile list</div>} />
        <Route
          path="/app/model-profiles/new"
          element={<ModelProfileEditor />}
        />
        <Route
          path="/app/model-profiles/:profileId/edit"
          element={<ModelProfileEditor />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ModelProfileEditor', () => {
  const create = jest.fn().mockResolvedValue(undefined);
  const update = jest.fn().mockResolvedValue(undefined);
  let spies: jest.SpyInstance[] = [];

  beforeEach(() => {
    jest.clearAllMocks();
    spies = [
      jest.spyOn(permissionsModule, 'usePermissionState').mockReturnValue({
        hasPermission: () => true,
        loading: false,
        currentUser: null,
      }),
      jest.spyOn(modelProfilesApi, 'useModelProfilesList').mockReturnValue({
        profiles: [profile],
        globalRunCostBudgetUsd: 1,
        loading: false,
        error: null,
        refresh: jest.fn(),
      }),
      jest.spyOn(modelProfilesApi, 'useModelProfileMutations').mockReturnValue({
        create,
        update,
        remove: jest.fn(),
        versions: jest.fn(),
      }),
    ];
  });

  afterEach(() => {
    cleanup();
    spies.forEach((spy) => spy.mockRestore());
  });

  it('creates a profile with a replaced cost cap and extra reasoning level', async () => {
    renderAt('/app/model-profiles/new');

    expect(
      screen.getByRole('heading', { level: 1, name: 'New model profile' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('textbox', { name: 'router model' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('textbox', { name: 'verifier model' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('textbox', { name: 'assistant model' }),
    ).not.toBeInTheDocument();

    const costCap = screen.getByRole('spinbutton', {
      name: /Run cost cap \(USD\)/,
    });
    fireEvent.change(costCap, { target: { value: '' } });
    expect(costCap).toHaveValue(null);
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();

    fireEvent.change(costCap, { target: { value: '2.5' } });
    expect(costCap).toHaveValue(2.5);
    expect(
      screen.getByText(/deployment-wide run cost cap is \$1/),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: /Name/ }), {
      target: { value: 'Careful' },
    });
    fireEvent.change(
      screen.getByRole('textbox', { name: /Primary model ID/ }),
      { target: { value: 'primary-model' } },
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: /Economy model ID/ }),
      { target: { value: 'economy-model' } },
    );
    const none = screen.getByRole('checkbox', { name: 'none' });
    expect(none).not.toBeChecked();
    fireEvent.click(none);
    fireEvent.click(
      screen.getByRole('radio', { name: 'Make high the default' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          run_cost_budget_usd: 2.5,
          user_reasoning_efforts: ['none', 'low', 'medium', 'high'],
          default_reasoning_effort: 'high',
        }),
      ),
    );
    expect(await screen.findByText('profile list')).toBeInTheDocument();
  });

  it('updates an existing profile without read-only record fields', async () => {
    renderAt('/app/model-profiles/profile-1/edit');

    expect(
      screen.getByRole('combobox', { name: 'worker summary reasoning' }),
    ).toHaveTextContent('minimal');
    expect(screen.getByText('Current version v3.')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'planner model' }), {
      target: { value: 'planner-model' },
    });
    expect(
      screen.getByText('Unsaved changes. Saving creates v4.'),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Reset worker summary' }),
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Version comment' }), {
      target: { value: 'Faster planner' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith(
      'profile-1',
      expect.objectContaining({
        name: 'Careful',
        comment: 'Faster planner',
        run_cost_budget_usd: 2.5,
        stage_overrides: {
          planner: { model_id: 'planner-model' },
        },
      }),
    );
    const payload = update.mock.calls[0][1];
    expect(payload).not.toHaveProperty('profile_id');
    expect(payload).not.toHaveProperty('current_version');
    expect(payload).not.toHaveProperty('created_at');
    expect(payload).not.toHaveProperty('updated_at');
    expect(payload).not.toHaveProperty('created_by');
    expect(payload).not.toHaveProperty('updated_by');
  });

  it('asks before discarding unsaved edits', () => {
    renderAt('/app/model-profiles/profile-1/edit');
    fireEvent.change(screen.getByRole('textbox', { name: /Name/ }), {
      target: { value: 'Renamed' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Back to model profiles' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.getByText('profile list')).toBeInTheDocument();
  });

  it('reports a profile that no longer exists', () => {
    renderAt('/app/model-profiles/missing/edit');
    expect(
      screen.getByText('This model profile no longer exists.'),
    ).toBeInTheDocument();
  });
});
