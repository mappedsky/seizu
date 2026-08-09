import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ChatSessionsPanel from 'src/components/ChatSessionsPanel';
import type { ChatSession } from 'src/hooks/useChatSessions';

const theme = createTheme();

function session(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    thread_id: 'thread-1',
    title: 'Session 1',
    created_at: '2024-01-01T00:00:00+00:00',
    updated_at: '2024-01-01T09:30:00+00:00',
    ...overrides,
  };
}

function renderPanel(sessions: ChatSession[]) {
  return render(
    <ThemeProvider theme={theme}>
      <ChatSessionsPanel
        open
        onToggle={jest.fn()}
        sessions={sessions}
        loading={false}
        activeThreadId="thread-1"
        onSelectSession={jest.fn()}
        onNewSession={jest.fn()}
        onDeleteSession={jest.fn()}
        onRenameSession={jest.fn()}
      />
    </ThemeProvider>,
  );
}

describe('ChatSessionsPanel', () => {
  afterEach(cleanup);

  it('keeps the last-activity time out of the row until it is hovered', async () => {
    renderPanel([session()]);

    const lastActivity = `Last activity ${new Date('2024-01-01T09:30:00+00:00').toLocaleString()}`;
    expect(screen.queryByText(lastActivity)).not.toBeInTheDocument();

    fireEvent.mouseOver(screen.getByText('Session 1'));

    expect(await screen.findByText(lastActivity)).toBeInTheDocument();
  });
});
