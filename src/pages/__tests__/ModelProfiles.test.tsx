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
      loading: false,
      error: null,
      refresh,
    });
    useModelProfileMutations.mockReturnValue({
      create,
      update: jest.fn(),
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

    fireEvent.change(screen.getByRole('textbox', { name: /Name/ }), {
      target: { value: 'Careful' },
    });
    const modelIds = screen.getAllByRole('textbox', { name: /Model ID/ });
    fireEvent.change(modelIds[0], { target: { value: 'primary-model' } });
    fireEvent.change(modelIds[1], { target: { value: 'economy-model' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ run_cost_budget_usd: 2.5 }),
      ),
    );
  });
});
