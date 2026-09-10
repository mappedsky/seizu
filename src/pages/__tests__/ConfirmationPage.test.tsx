import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import * as confirmationsApi from 'src/hooks/useConfirmationsApi';
import ConfirmationPage from 'src/pages/ConfirmationPage';
import { AuthContext } from 'src/auth.context';

const denied: confirmationsApi.ActionConfirmation = {
  confirmation_id: 'c1',
  source: 'mcp',
  tool_name: 'reports__delete',
  action: 'delete',
  resource_type: 'report',
  resource_id: 'r1',
  arguments: {},
  status: 'denied',
  created_at: '2026-01-01T00:00:00Z',
  expires_at: '2099-01-01T00:00:00Z',
};

let spy: jest.SpyInstance;
const decideConfirmation = jest.fn();
const getConfirmation = jest.fn();

beforeEach(() => {
  getConfirmation.mockResolvedValue(denied);
  decideConfirmation.mockResolvedValue({ ...denied, status: 'approved' });
  spy = jest.spyOn(confirmationsApi, 'useConfirmationsApi').mockReturnValue({
    confirmations: [],
    loading: false,
    error: null,
    fetchConfirmations: jest.fn(),
    getConfirmation,
    getConfirmationsByBatchId: jest.fn(),
    decideConfirmation,
  });
});

afterEach(() => {
  cleanup();
  spy.mockRestore();
  jest.clearAllMocks();
});

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/app/confirmations/c1']}>
      <AuthContext.Provider
        value={{ accessToken: 'test-token', isLoading: false }}
      >
        <Routes>
          <Route
            path="/app/confirmations/:confirmationId"
            element={<ConfirmationPage />}
          />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

test('owner can allow a previously denied action', async () => {
  renderPage();
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Allow previously denied action',
    }),
  );
  await waitFor(() =>
    expect(decideConfirmation).toHaveBeenCalledWith('c1', 'approved'),
  );
  expect(await screen.findByText('Status: approved')).toBeInTheDocument();
});

test('expired denials cannot be reversed', async () => {
  getConfirmation.mockResolvedValue({
    ...denied,
    expires_at: '2000-01-01T00:00:00Z',
  });
  renderPage();
  await screen.findByText('Status: denied');
  expect(
    screen.queryByRole('button', { name: 'Allow previously denied action' }),
  ).not.toBeInTheDocument();
});
