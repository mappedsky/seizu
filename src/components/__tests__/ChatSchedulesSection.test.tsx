import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ChatSchedulesSection from 'src/components/ChatSchedulesSection';
import { AuthConfigContext } from 'src/authConfig.context';

const theme = createTheme();

const SCHEDULE = {
  scheduled_chat_id: 'sc1',
  name: 'Daily CVE digest',
  prompt: 'Summarize new critical CVEs',
  schedule: { type: 'daily' as const, days_of_week: [0, 2], hour: 9 },
  watch_scans: [],
  enabled: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  created_by: 'user-1',
  last_run_status: 'success',
  last_run_at: '2026-01-02T00:00:00Z',
  last_errors: [],
};

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <AuthConfigContext.Provider
      value={{ auth_required: false, oidc: null, loaded: true }}
    >
      <ThemeProvider theme={theme}>{children}</ThemeProvider>
    </AuthConfigContext.Provider>
  );
}

describe('ChatSchedulesSection', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ schedules: [SCHEDULE] }),
    } as Response);
  });

  afterEach(() => {
    cleanup();
    jest.restoreAllMocks();
  });

  it('lists schedules with trigger and status', async () => {
    render(<ChatSchedulesSection enabled />, { wrapper: Wrapper });
    await act(async () => {});

    expect(screen.getByText('SCHEDULES')).toBeInTheDocument();
    expect(screen.getByText('Daily CVE digest')).toBeInTheDocument();
    expect(
      screen.getByText('Mon, Wed at 09:00 UTC · success'),
    ).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith('/api/v1/chat/schedules', {
      headers: {},
    });
  });

  it('opens the create dialog from the add button', async () => {
    render(<ChatSchedulesSection enabled />, { wrapper: Wrapper });
    await act(async () => {});

    fireEvent.click(screen.getByRole('button', { name: 'New scheduled chat' }));

    expect(await screen.findByText('New scheduled chat')).toBeInTheDocument();
    expect(screen.getByLabelText('Name')).toBeInTheDocument();
    expect(screen.getByLabelText('Prompt')).toBeInTheDocument();
    expect(screen.getByLabelText('Schedule')).toBeChecked();
  });

  it('opens the edit dialog when a schedule is clicked', async () => {
    render(<ChatSchedulesSection enabled />, { wrapper: Wrapper });
    await act(async () => {});

    fireEvent.click(screen.getByText('Daily CVE digest'));

    expect(await screen.findByText('Edit scheduled chat')).toBeInTheDocument();
    expect(screen.getByLabelText('Prompt')).toHaveValue(
      'Summarize new critical CVEs',
    );
  });

  it('warns when monthly days that not all months have are selected', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        schedules: [
          {
            ...SCHEDULE,
            schedule: { type: 'monthly' as const, days_of_month: [15, 31] },
          },
        ],
      }),
    } as Response);

    render(<ChatSchedulesSection enabled />, { wrapper: Wrapper });
    await act(async () => {});

    fireEvent.click(screen.getByText('Daily CVE digest'));
    await screen.findByText('Edit scheduled chat');

    expect(
      screen.getByText(
        /in those months the chat runs on the last day of the month instead/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/do not have day 31/)).toBeInTheDocument();
  });
});
