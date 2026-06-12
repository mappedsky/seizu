import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ScheduledChats from 'src/pages/ScheduledChats';
import { FeaturesContext } from 'src/features.context';
import * as schedulesModule from 'src/hooks/useChatSchedules';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

jest.mock('src/hooks/useChatSchedules', () => ({
  useChatSchedules: jest.fn(),
  useScheduledChatSessions: jest.fn(),
  useScheduledChatSessionHistory: jest.fn(),
}));

const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;
const mockUseChatSchedules =
  schedulesModule.useChatSchedules as unknown as jest.Mock;

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

  it('navigates to the detail view when a name is clicked', () => {
    render(
      <Routes>
        <Route path="/app/scheduled-chats" element={<ScheduledChats />} />
        <Route
          path="/app/scheduled-chats/:id"
          element={<div>view probe</div>}
        />
      </Routes>,
      { wrapper: Wrapper },
    );

    fireEvent.click(screen.getByText('Daily CVE digest'));

    expect(screen.getByText('view probe')).toBeInTheDocument();
  });

  it('hides the page content without the permission', () => {
    mockUsePermissions.mockReturnValue(() => false);
    render(<ScheduledChats />, { wrapper: Wrapper });

    expect(
      screen.getByText('Scheduled chats are not available for your account.'),
    ).toBeInTheDocument();
  });

  it('hides the all-users toggle without the read_all permission', () => {
    render(<ScheduledChats />, { wrapper: Wrapper });

    expect(screen.queryByText('Show all users')).not.toBeInTheDocument();
  });

  it('shows all users with an owner column and filter for admins', async () => {
    mockUsePermissions.mockReturnValue(
      (permission: string) =>
        permission === 'chat:schedule' ||
        permission === 'chat:schedule:read_all',
    );
    mockUseChatSchedules.mockReturnValue({
      schedules: [
        SCHEDULE,
        {
          ...SCHEDULE,
          scheduled_chat_id: 'sc2',
          name: 'Weekly posture',
          created_by: 'user-2',
        },
      ],
      loading: false,
      error: null,
      refresh: jest.fn(),
      createSchedule: jest.fn(),
      updateSchedule: jest.fn(),
      deleteSchedule: jest.fn(),
    });

    render(<ScheduledChats />, { wrapper: Wrapper });

    fireEvent.click(screen.getByLabelText('Show all users'));
    expect(mockUseChatSchedules).toHaveBeenLastCalledWith(true, {
      all: true,
    });

    // Owner column appears in all mode.
    expect(screen.getByText('Owner')).toBeInTheDocument();

    // Filter the list down to user-2 via the table's facet filter menu
    // (same UI as the reports list).
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    expect(await screen.findByText('User')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('menuitem', { name: 'user-2' }));

    expect(screen.getByText('Weekly posture')).toBeInTheDocument();
    expect(screen.queryByText('Daily CVE digest')).not.toBeInTheDocument();
  });
});
