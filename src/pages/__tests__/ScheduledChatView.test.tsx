import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ScheduledChatView from 'src/pages/ScheduledChatView';
import * as schedulesModule from 'src/hooks/useChatSchedules';
import * as currentUserModule from 'src/hooks/useCurrentUser';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

jest.mock('src/hooks/useCurrentUser', () => ({
  useCurrentUserState: jest.fn(),
}));

jest.mock('src/hooks/useChatSchedules', () => ({
  useChatSchedule: jest.fn(),
  useChatSchedules: jest.fn(),
  useScheduledChatSessions: jest.fn(),
  useScheduledChatSessionHistory: jest.fn(),
}));

jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: ({ userId }: { userId: string }) => <>{userId}</>,
}));

const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;
const mockUseCurrentUserState =
  currentUserModule.useCurrentUserState as unknown as jest.Mock;
const mockUseChatSchedule =
  schedulesModule.useChatSchedule as unknown as jest.Mock;
const mockUseChatSchedules =
  schedulesModule.useChatSchedules as unknown as jest.Mock;
const mockUseScheduledChatSessions =
  schedulesModule.useScheduledChatSessions as unknown as jest.Mock;
const mockUseSessionHistory =
  schedulesModule.useScheduledChatSessionHistory as unknown as jest.Mock;

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

function makeWrapper(state: Record<string, unknown> = {}) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <MemoryRouter
        initialEntries={[{ pathname: '/app/scheduled-chats/sc1', state }]}
      >
        <ThemeProvider theme={theme}>
          <Routes>
            <Route path="/app/scheduled-chats/:id" element={<>{children}</>} />
          </Routes>
        </ThemeProvider>
      </MemoryRouter>
    );
  };
}

const Wrapper = makeWrapper();

describe('ScheduledChatView', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUsePermissions.mockReturnValue(
      (permission: string) => permission === 'chat:schedule',
    );
    mockUseCurrentUserState.mockReturnValue({
      currentUser: { user_id: 'user-1', permissions: ['chat:schedule'] },
      loading: false,
    });
    mockUseChatSchedule.mockReturnValue({
      schedule: SCHEDULE,
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseChatSchedules.mockReturnValue({
      schedules: [],
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
          run_status: 'partial',
          run_errors: [
            'Planner structured output failed; used a single-step fallback.',
          ],
        },
      ]),
    );
    mockUseSessionHistory.mockReturnValue(
      jest.fn().mockResolvedValue([
        { id: 'm1', role: 'user', text: 'Summarize new critical CVEs' },
        {
          id: 'm2',
          role: 'assistant',
          text: 'Digest complete.',
          metadata: {
            details: [
              {
                kind: 'thinking',
                title: 'Planning the digest',
                status: 'completed',
              },
              {
                kind: 'tool',
                title: 'Tool: graph__schema',
                status: 'completed',
                arguments: '{}',
                body: '{"labels":["CVE"]}',
              },
            ],
          },
        },
      ]),
    );
  });

  afterEach(() => {
    cleanup();
    jest.restoreAllMocks();
  });

  it('shows detail panels, the edit button, and the actions menu', async () => {
    render(<ScheduledChatView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(
      screen.getByRole('heading', { name: 'Daily CVE digest' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'More actions' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Mon, Wed at 09:00 UTC')).toBeInTheDocument();
    expect(screen.getByText('Summarize new critical CVEs')).toBeInTheDocument();
    expect(screen.getByText('v3')).toBeInTheDocument();
    expect(screen.getByText('user-1')).toBeInTheDocument();
  });

  it('expands a run to show the transcript with a details section', async () => {
    render(<ScheduledChatView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(screen.getByText('partial')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Daily CVE digest – 2026-01-02'));
    await act(async () => {});

    expect(
      screen.getByText(
        'Planner structured output failed; used a single-step fallback.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Digest complete.')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Details (2)'));

    expect(screen.getByText('Planning the digest')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Tool: graph__schema'));
    expect(screen.getByText('{"labels":["CVE"]}')).toBeInTheDocument();
  });

  it('disables edit and delete for non-owners', async () => {
    mockUseCurrentUserState.mockReturnValue({
      currentUser: { user_id: 'someone-else', permissions: ['chat:schedule'] },
      loading: false,
    });
    render(<ScheduledChatView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled();
  });

  it('shows a back button when fromLabel is in navigation state', async () => {
    const WrapperWithState = makeWrapper({ fromLabel: 'Scheduled Chats' });
    render(<ScheduledChatView />, { wrapper: WrapperWithState });
    await act(async () => {});

    expect(
      screen.getByRole('button', { name: /back to scheduled chats/i }),
    ).toBeInTheDocument();
  });

  it('omits the back button without fromLabel in navigation state', async () => {
    render(<ScheduledChatView />, { wrapper: Wrapper });
    await act(async () => {});

    expect(
      screen.queryByRole('button', { name: /back to/i }),
    ).not.toBeInTheDocument();
  });
});
