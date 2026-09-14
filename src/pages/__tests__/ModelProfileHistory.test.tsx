import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ModelProfileHistory from 'src/pages/ModelProfileHistory';
import * as permissionsModule from 'src/hooks/usePermissions';
import type { ModelProfileVersion } from 'src/hooks/useModelProfilesApi';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));
jest.mock('src/hooks/useUser', () => ({ useUser: jest.fn(() => null) }));
jest.mock('src/hooks/useAuthHeaders', () => ({
  useAuthHeaders: () => ({
    authReady: true,
    authHeaders: () => ({}),
    checkAuthReady: () => true,
  }),
}));
const permissions = permissionsModule.usePermissionState as jest.MockedFunction<
  typeof permissionsModule.usePermissionState
>;
const original: ModelProfileVersion = {
  profile_id: 'profile-1',
  version: 1,
  name: 'Original profile',
  description: 'Original settings',
  enabled: true,
  is_default: false,
  primary: { model_id: 'original-primary' },
  economy: { model_id: 'original-economy', reasoning_effort: 'low' },
  stage_overrides: {
    verifier: { model_id: 'verifier-model', reasoning_effort: 'high' },
  },
  user_reasoning_efforts: ['low', 'high'],
  default_reasoning_effort: 'high',
  run_cost_budget_usd: 2,
  created_at: '2026-08-25T00:00:00Z',
  created_by: 'user-1',
  comment: 'Initial revision',
};
const current: ModelProfileVersion = {
  ...original,
  version: 2,
  name: 'Current profile',
  primary: { model_id: 'current-primary' },
  comment: 'New model',
};

function renderHistory() {
  return render(
    <MemoryRouter initialEntries={['/app/model-profiles/profile-1/history']}>
      <Routes>
        <Route
          path="/app/model-profiles/:profileId/history"
          element={<ModelProfileHistory />}
        />
        <Route path="/app/model-profiles" element={<div>Profile list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ModelProfileHistory', () => {
  let fetchMock: jest.SpyInstance;
  let revisions: ModelProfileVersion[];
  beforeEach(() => {
    permissions.mockReturnValue({
      hasPermission: () => true,
      loading: false,
      currentUser: null,
    });
    revisions = [original, current];
    fetchMock = jest
      .spyOn(global, 'fetch')
      .mockImplementation(async (_input, init) => {
        if (init?.method === 'PUT') {
          const payload = JSON.parse(String(init.body));
          const restored = { ...original, ...payload, version: 3 };
          revisions = [...revisions, restored];
          return new Response(
            JSON.stringify({ ...restored, current_version: 3 }),
          );
        }
        return new Response(JSON.stringify({ versions: revisions }));
      });
  });
  afterEach(() => {
    cleanup();
    fetchMock.mockRestore();
  });

  it('shows a revision in the shared detail view and marks the current version', async () => {
    renderHistory();
    await screen.findByText('Current profile');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      'Version history – Current profile',
    );
    expect(screen.getByText('current')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Actions for version 2' }),
    );
    expect(screen.getByRole('menuitem', { name: 'Restore' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'View' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('current-primary')).toBeInTheDocument();
    expect(within(dialog).getByText('original-economy')).toBeInTheDocument();
    expect(within(dialog).getByText(/verifier-model/)).toBeInTheDocument();
  });

  it('restores editable settings as a new revision and refreshes history', async () => {
    renderHistory();
    await screen.findByText('Current profile');
    fireEvent.click(
      screen.getByRole('button', { name: 'Actions for version 1' }),
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('menuitem', { name: 'Restore' }));
    });
    await screen.findByText('v3');
    const write = fetchMock.mock.calls.find(
      ([, init]) => init?.method === 'PUT',
    );
    expect(write[0]).toBe('/api/v1/model-profiles/profile-1');
    const {
      profile_id: _profileId,
      version: _version,
      created_at: _createdAt,
      created_by: _createdBy,
      comment: _comment,
      ...settings
    } = original;
    expect(JSON.parse(write[1].body)).toEqual({
      ...settings,
      comment: 'Restored from version 1',
    });
    expect(screen.getByText('v1')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('Restored from version 1')).toBeInTheDocument();
  });

  it('keeps read-only revisions viewable without restore permission', async () => {
    permissions.mockReturnValue({
      hasPermission: (permission) => permission === 'model_profiles:read',
      loading: false,
      currentUser: null,
    });
    renderHistory();
    await screen.findByText('Current profile');
    fireEvent.click(
      screen.getByRole('button', { name: 'Actions for version 1' }),
    );
    expect(screen.getByRole('menuitem', { name: 'Restore' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'View' }));
    expect(
      within(screen.getByRole('dialog')).getByText('original-primary'),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.every(([, init]) => init?.method !== 'PUT'),
    ).toBe(true);
  });

  it('shows restore failures and leaves the history available for retry', async () => {
    renderHistory();
    await screen.findByText('Current profile');
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Cannot restore this profile' }), {
        status: 409,
      }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Actions for version 1' }),
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('menuitem', { name: 'Restore' }));
    });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Cannot restore this profile',
    );
    expect(screen.getByText('v2')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Actions for version 1' }),
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('menuitem', { name: 'Restore' }));
    });
    await waitFor(() => expect(screen.getByText('v3')).toBeInTheDocument());
  });
});
