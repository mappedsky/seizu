import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import ModelProfiles from 'src/pages/ModelProfiles';
import * as permissionsModule from 'src/hooks/usePermissions';
import * as modelProfilesApi from 'src/hooks/useModelProfilesApi';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));

jest.mock('src/hooks/useModelProfilesApi', () => ({
  useModelProfilesList: jest.fn(),
  useModelProfileMutations: jest.fn(),
}));

const usePermissionState =
  permissionsModule.usePermissionState as jest.MockedFunction<
    typeof permissionsModule.usePermissionState
  >;
const useModelProfilesList =
  modelProfilesApi.useModelProfilesList as jest.MockedFunction<
    typeof modelProfilesApi.useModelProfilesList
  >;
const useModelProfileMutations =
  modelProfilesApi.useModelProfileMutations as jest.MockedFunction<
    typeof modelProfilesApi.useModelProfileMutations
  >;

describe('ModelProfiles', () => {
  const create = jest.fn().mockResolvedValue(undefined);
  const update = jest.fn().mockResolvedValue(undefined);
  const refresh = jest.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    jest.clearAllMocks();
    usePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: false,
      currentUser: null,
    });
    useModelProfilesList.mockReturnValue({
      profiles: [],
      globalRunCostBudgetUsd: 1,
      loading: false,
      error: null,
      refresh,
    });
    useModelProfileMutations.mockReturnValue({
      create,
      update,
      remove: jest.fn(),
      versions: jest.fn(),
    });
  });

  afterEach(cleanup);

  it('allows the run cost cap to be cleared and replaced', async () => {
    render(<ModelProfiles />);
    fireEvent.click(screen.getByRole('button', { name: 'New profile' }));

    const costCap = screen.getByRole('spinbutton', {
      name: /Run cost cap \(USD\)/,
    });
    fireEvent.change(costCap, { target: { value: '' } });
    expect(costCap).toHaveValue(null);

    fireEvent.change(costCap, { target: { value: '2.5' } });
    expect(costCap).toHaveValue(2.5);
    expect(
      screen.getByText(/deployment-wide run cost cap is \$1/),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: /Name/ }), {
      target: { value: 'Careful' },
    });
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Primary model ID' }),
      { target: { value: 'primary-model' } },
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Economy model ID' }),
      { target: { value: 'economy-model' } },
    );
    const none = screen.getByRole('checkbox', { name: 'none' });
    expect(none).not.toBeChecked();
    fireEvent.click(none);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          run_cost_budget_usd: 2.5,
          user_reasoning_efforts: ['none', 'low', 'medium', 'high'],
        }),
      ),
    );
  });

  it('omits read-only record fields when updating a profile', async () => {
    useModelProfilesList.mockReturnValue({
      profiles: [
        {
          profile_id: 'profile-1',
          name: 'Careful',
          description: 'A model profile',
          enabled: true,
          is_default: true,
          primary: {
            model_id: 'primary-model',
          },
          economy: {
            model_id: 'economy-model',
            reasoning_effort: 'low',
          },
          stage_overrides: {
            worker_summary: {
              reasoning_effort: 'minimal',
            },
          },
          user_reasoning_efforts: ['low', 'medium', 'high'],
          default_reasoning_effort: 'medium',
          run_cost_budget_usd: 2.5,
          current_version: 3,
          created_at: '2026-08-25T00:00:00Z',
          updated_at: '2026-08-26T00:00:00Z',
          created_by: 'admin',
          updated_by: 'admin',
        },
      ],
      globalRunCostBudgetUsd: 1,
      loading: false,
      error: null,
      refresh,
    });
    render(<ModelProfiles />);
    expect(screen.getByText('Limited to $1 globally')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Careful' }));
    expect(
      screen.getByRole('combobox', {
        name: 'worker summary reasoning',
      }),
    ).toHaveTextContent('minimal');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith(
      'profile-1',
      expect.objectContaining({
        name: 'Careful',
        run_cost_budget_usd: 2.5,
        stage_overrides: expect.objectContaining({
          worker_summary: expect.objectContaining({
            reasoning_effort: 'minimal',
          }),
        }),
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
});
