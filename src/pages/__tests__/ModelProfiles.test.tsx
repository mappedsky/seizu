import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import ModelProfiles from 'src/pages/ModelProfiles';
import { MemoryRouter } from 'react-router-dom';
import * as permissionsModule from 'src/hooks/usePermissions';
import * as modelProfilesApi from 'src/hooks/useModelProfilesApi';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));

jest.mock('src/hooks/useModelProfilesApi', () => ({
  useSelectableModelProfiles: jest.fn(() => ({
    profiles: [],
    defaultProfileId: null,
    loading: false,
    error: null,
    refresh: jest.fn(),
  })),
  useModelProfilesList: jest.fn(),
  useModelProfileVersionsList: jest.fn(),
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
    render(
      <MemoryRouter>
        <ModelProfiles />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'New profile' }));

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
    render(
      <MemoryRouter>
        <ModelProfiles />
      </MemoryRouter>,
    );
    expect(screen.getByText('Limited to $1 globally')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { level: 1, name: 'Model profiles' }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Actions for Careful' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }));
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

  it('confirms deletion and keeps a failed delete open for retry', async () => {
    const profile: modelProfilesApi.ModelProfile = {
      profile_id: 'profile-1',
      name: 'Careful',
      description: '',
      enabled: true,
      is_default: false,
      primary: { model_id: 'primary-model' },
      economy: { model_id: 'economy-model', reasoning_effort: 'low' },
      stage_overrides: {},
      user_reasoning_efforts: ['low'],
      default_reasoning_effort: 'low',
      run_cost_budget_usd: 1,
      current_version: 1,
      created_at: '2026-08-25T00:00:00Z',
      updated_at: '2026-08-25T00:00:00Z',
      created_by: 'admin',
    };
    const remove = jest
      .fn()
      .mockRejectedValueOnce(new Error('Delete failed'))
      .mockResolvedValue(undefined);
    useModelProfileMutations.mockReturnValue({
      create,
      update,
      remove,
      versions: jest.fn(),
    });
    useModelProfilesList.mockReturnValue({
      profiles: [profile],
      globalRunCostBudgetUsd: 1,
      loading: false,
      error: null,
      refresh,
    });
    render(
      <MemoryRouter>
        <ModelProfiles />
      </MemoryRouter>,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Actions for Careful' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    const dialog = screen.getByRole('dialog', {
      name: 'Delete model profile?',
    });
    expect(remove).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    });
    expect(
      await within(dialog).findByText('Delete failed'),
    ).toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    });
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    expect(remove).toHaveBeenNthCalledWith(2, 'profile-1');
  });
});
