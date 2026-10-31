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
import { MemoryRouter, Route, Routes } from 'react-router-dom';
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
  const create = jest.fn();
  const update = jest.fn();
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

  it('opens the editor page for new and existing profiles', () => {
    useModelProfilesList.mockReturnValue({
      profiles: [
        {
          profile_id: 'profile-1',
          name: 'Careful',
          description: '',
          enabled: true,
          is_default: true,
          primary: { model_id: 'primary-model' },
          economy: { model_id: 'economy-model', reasoning_effort: 'low' },
          stage_overrides: {},
          user_reasoning_efforts: ['low'],
          default_reasoning_effort: 'low',
          run_cost_budget_usd: 2.5,
          current_version: 3,
          created_at: '2026-08-25T00:00:00Z',
          updated_at: '2026-08-26T00:00:00Z',
          created_by: 'admin',
        },
      ],
      globalRunCostBudgetUsd: 1,
      loading: false,
      error: null,
      refresh,
    });
    render(
      <MemoryRouter initialEntries={['/app/model-profiles']}>
        <Routes>
          <Route path="/app/model-profiles" element={<ModelProfiles />} />
          <Route
            path="/app/model-profiles/new"
            element={<div>new editor</div>}
          />
          <Route
            path="/app/model-profiles/:profileId/edit"
            element={<div>edit editor</div>}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText('Limited to $1 globally')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Actions for Careful' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }));
    expect(screen.getByText('edit editor')).toBeInTheDocument();
  });

  it('routes New profile to the editor page', () => {
    render(
      <MemoryRouter initialEntries={['/app/model-profiles']}>
        <Routes>
          <Route path="/app/model-profiles" element={<ModelProfiles />} />
          <Route
            path="/app/model-profiles/new"
            element={<div>new editor</div>}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(
      screen.getByRole('heading', { level: 1, name: 'Model profiles' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'New profile' }));
    expect(screen.getByText('new editor')).toBeInTheDocument();
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
