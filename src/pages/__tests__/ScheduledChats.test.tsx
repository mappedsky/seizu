import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ScheduledChats from 'src/pages/ScheduledChats';
import { FeaturesContext } from 'src/features.context';
import * as schedulesModule from 'src/hooks/useChatSchedules';
import * as historyModule from 'src/hooks/useChatHistory';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/hooks/useChatSchedules', () => ({
  useChatSchedules: jest.fn(),
  useScheduledChatSessions: jest.fn(),
}));

jest.mock('src/hooks/useChatHistory', () => ({
  useChatHistory: jest.fn(),
}));

const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;
const mockUseChatSchedules =
  schedulesModule.useChatSchedules as unknown as jest.Mock;
const mockUseScheduledChatSessions =
  schedulesModule.useScheduledChatSessions as unknown as jest.Mock;
const mockUseChatHistory = historyModule.useChatHistory as unknown as jest.Mock;

const theme = createTheme();

const SCHEDULE: schedulesModule.ScheduledChat = {
  scheduled_chat_id: 'sc1',
  name: 'Daily CVE digest',
  prompt: 'Summarize new critical CVEs',
  schedule: { type: 'daily', days_of_week: [0, 2], hour: 9 },
  watch_scans: [],
  enabled: true,
  current_version: 3,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  created_by: 'user-1',
  updated_by: 'user-1',
  last_run_status: 'success',
  last_run_at: '2026-01-02T00:00:00Z',
  last_errors: [],
};

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/scheduled-chats']}>
      <FeaturesContext.Provider value={{ chat: true, chat_schedules: true }}>
        <ThemeProvider theme={theme}>{children}</ThemeProvider>
      </FeaturesContext.Provider>
    </MemoryRouter>
  );
}

describe('ScheduledChats', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUsePermissions.mockReturnValue(
      (permission: string) => permission === 'chat:schedule',
    );
    mockUseChatSchedules.mockReturnValue({
      schedules: [SCHEDULE],
      loading: false,
      error: null,
      refresh: jest.fn(),
      createSchedule: jest.fn(),
      updateSchedule: jest.fn(),
      deleteSchedule: jest.fn(),
    });
    mockUseScheduledChatSessions.mockReturnValue(
      jest.fn().mockResolvedValue([
        {
          thread_id: '12345',
          title: 'Daily CVE digest – 2026-01-02',
          created_at: '2026-01-02T00:00:00Z',
          updated_at: '2026-01-02T00:00:05Z',
        },
      ]),
    );
    mockUseChatHistory.mockReturnValue(
      jest.fn().mockResolvedValue([
        {
          id: 'm1',
          role: 'assistant',
          parts: [{ type: 'text', text: 'Digest complete.' }],
        },
      ]),
    );
  });

  afterEach(() => {
    cleanup();
    jest.restoreAllMocks();
  });

  it('lists schedules with trigger, status, and version', () => {
    render(<ScheduledChats />, { wrapper: Wrapper });

    expect(screen.getByText('Scheduled Chats')).toBeInTheDocument();
    expect(screen.getByText('Daily CVE digest')).toBeInTheDocument();
    expect(screen.getByText('Mon, Wed at 09:00 UTC')).toBeInTheDocument();
    expect(screen.getByText('success')).toBeInTheDocument();
    expect(screen.getByText('v3')).toBeInTheDocument();
  });

  it('shows the runs dialog with a read-only transcript', async () => {
    render(<ScheduledChats />, { wrapper: Wrapper });

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(await screen.findByText('View runs'));
    await act(async () => {});

    expect(screen.getByText('Runs – Daily CVE digest')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Daily CVE digest – 2026-01-02'));
    await act(async () => {});

    expect(screen.getByText('read-only')).toBeInTheDocument();
    expect(screen.getByText('Digest complete.')).toBeInTheDocument();
  });

  it('hides the page content without the permission', () => {
    mockUsePermissions.mockReturnValue(() => false);
    render(<ScheduledChats />, { wrapper: Wrapper });

    expect(
      screen.getByText('Scheduled chats are not available for your account.'),
    ).toBeInTheDocument();
  });
});
