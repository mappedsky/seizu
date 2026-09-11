import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { FeaturesContext } from 'src/features.context';
import * as permissions from 'src/hooks/usePermissions';
import ChatConnections from 'src/pages/ChatConnections';

const connection = {
  proxy_name: 'gateway',
  status: 'authorization_required',
  observed_at: '2026-09-07T00:00:00+00:00',
  error_code: 'authorization_required',
  reauthorize_url: 'https://gateway.test/accounts',
};

function renderPage(
  token: string | null = 'browser-token',
  connectionsEnabled = true,
) {
  return render(
    <MemoryRouter>
      <FeaturesContext.Provider
        value={{
          chat: true,
          chat_schedules: true,
          chat_connections: connectionsEnabled,
        }}
      >
        <AuthConfigContext.Provider
          value={{ auth_required: true, oidc: null, loaded: true }}
        >
          <AuthContext.Provider
            value={{ accessToken: token, isLoading: false }}
          >
            <ChatConnections />
          </AuthContext.Provider>
        </AuthConfigContext.Provider>
      </FeaturesContext.Provider>
    </MemoryRouter>,
  );
}

describe('Chat connections', () => {
  const originalFetch = global.fetch;
  let permissionSpy: jest.SpyInstance;
  let fetchMock: jest.Mock;

  beforeEach(() => {
    permissionSpy = jest
      .spyOn(permissions, 'usePermissionState')
      .mockReturnValue({
        hasPermission: () => true,
        loading: false,
        currentUser: null,
      });
    fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ connections: [connection] }),
    });
    global.fetch = fetchMock;
  });

  afterEach(() => {
    cleanup();
    permissionSpy.mockRestore();
    global.fetch = originalFetch;
  });

  it('shows the gateway link and checks recovery with authenticated CSRF protection', async () => {
    renderPage();
    expect(
      screen.getByText(
        /Experimental: per-user gateway delegation and recovery/,
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole('link', { name: 'Reauthorize' }),
    ).toHaveAttribute('href', 'https://gateway.test/accounts');
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...connection,
        status: 'connected',
        error_code: null,
      }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(await screen.findByText('Connected')).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Reauthorize' }),
    ).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/chat/connections/gateway/check',
      expect.objectContaining({
        method: 'POST',
        headers: { Authorization: 'Bearer browser-token', 'X-Seizu-Csrf': '1' },
      }),
    );
  });

  it('directs service failures to an administrator without offering user consent', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        connections: [
          { ...connection, status: 'service_authentication_failed' },
        ],
      }),
    });
    renderPage();
    expect(await screen.findByText(/Ask an administrator/)).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Reauthorize' }),
    ).not.toBeInTheDocument();
  });

  it('keeps the durable status visible when checking fails', async () => {
    renderPage();
    await screen.findByText('Reauthorization required');
    fetchMock.mockResolvedValueOnce({ ok: false });
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(
      await screen.findByText('Could not check the connection.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Reauthorization required')).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Check connection' }),
      ).not.toBeDisabled(),
    );
  });

  it('does not request connections without chat permission', () => {
    permissionSpy.mockReturnValue({
      hasPermission: () => false,
      loading: false,
      currentUser: null,
    });
    renderPage();
    expect(
      screen.getByText('Chat connections are unavailable.'),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does not request connections when no gateway is configured', () => {
    renderPage('browser-token', false);
    expect(
      screen.getByText('Chat connections are unavailable.'),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('waits for a browser access token', () => {
    renderPage(null);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows the gateway explanation and destination without automatically navigating', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        connections: [
          {
            ...connection,
            status: 'interaction_required',
            elicitations: [
              {
                elicitation_id: 'opaque',
                url: 'https://gateway.test/connect?nonce=private',
                message: '<script>Connect your account</script>',
              },
            ],
          },
        ],
      }),
    });
    renderPage();
    const link = await screen.findByRole('link', { name: 'Open gateway.test' });
    expect(link).toHaveAttribute(
      'href',
      'https://gateway.test/connect?nonce=private',
    );
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(
      screen.getByText('<script>Connect your account</script>'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Reauthorize' }),
    ).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('explains how to recover when elicitation links have expired or were rejected', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        connections: [
          { ...connection, status: 'interaction_required', elicitations: [] },
        ],
      }),
    });
    renderPage();
    expect(
      await screen.findByText(/No current approved link/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: /Open gateway/ }),
    ).not.toBeInTheDocument();
  });
});
