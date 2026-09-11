import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import * as confirmationsApi from 'src/hooks/useConfirmationsApi';
import BatchConfirmationPage from 'src/pages/BatchConfirmationPage';
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
  batch_id: 'b1',
  created_at: '2026-01-01T00:00:00Z',
  expires_at: '2099-01-01T00:00:00Z',
};

let spy: jest.SpyInstance;
const decideConfirmation = jest.fn();
const getConfirmationsByBatchId = jest.fn();

beforeEach(() => {
  getConfirmationsByBatchId.mockResolvedValue([denied]);
  decideConfirmation.mockResolvedValue({ ...denied, status: 'approved' });
  spy = jest.spyOn(confirmationsApi, 'useConfirmationsApi').mockReturnValue({
    confirmations: [],
    loading: false,
    error: null,
    fetchConfirmations: jest.fn(),
    getConfirmation: jest.fn(),
    getConfirmationsByBatchId,
    decideConfirmation,
  });
});

afterEach(() => {
  cleanup();
  spy.mockRestore();
  jest.clearAllMocks();
});

test('MCP batch confirmation can approve a live denied action', async () => {
  render(
    <MemoryRouter initialEntries={['/app/confirmations/batch/b1']}>
      <AuthContext.Provider
        value={{ accessToken: 'test-token', isLoading: false }}
      >
        <Routes>
          <Route
            path="/app/confirmations/batch/:batchId"
            element={<BatchConfirmationPage />}
          />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Accept',
    }),
  );

  await waitFor(() =>
    expect(decideConfirmation).toHaveBeenCalledWith('c1', 'approved'),
  );
});
