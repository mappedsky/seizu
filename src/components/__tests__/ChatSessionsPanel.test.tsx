import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ChatSessionsPanel from 'src/components/ChatSessionsPanel';
import { DEFAULT_FEATURES, FeaturesContext } from 'src/features.context';
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

function renderPanel(
  sessions: ChatSession[],
  {
    open = true,
    connections = false,
  }: { open?: boolean; connections?: boolean } = {},
) {
  return render(
    <MemoryRouter>
      <FeaturesContext.Provider
        value={{
          ...DEFAULT_FEATURES,
          chat: true,
          chat_connections: connections,
        }}
      >
        <ThemeProvider theme={theme}>
          <ChatSessionsPanel
            open={open}
            onToggle={jest.fn()}
            sessions={sessions}
            loading={false}
            activeThreadId="thread-1"
            onSelectSession={jest.fn()}
            onNewSession={jest.fn()}
            onDeleteSession={jest.fn()}
            onRenameSession={jest.fn()}
          />
        </ThemeProvider>
      </FeaturesContext.Provider>
    </MemoryRouter>,
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

  // Chat's own configuration lives at the foot of its panel, the way a space
  // keeps its own — not in the product's top-level navigation.
  it('offers connections below the sessions when a gateway delegates per user', () => {
    renderPanel([session()], { connections: true });

    // Carrying the conversation, so the page returns to it rather than to the
    // landing.
    expect(screen.getByRole('link', { name: 'Connections' })).toHaveAttribute(
      'href',
      '/app/chat/connections?from=thread-1',
    );
  });

  it('has nothing to configure when no gateway delegates per user', () => {
    renderPanel([session()]);

    expect(
      screen.queryByRole('link', { name: 'Connections' }),
    ).not.toBeInTheDocument();
  });

  // The collapsed panel is 40px of icons; the entry stays reachable there
  // rather than requiring the panel be reopened to find it.
  it('keeps connections reachable while the panel is collapsed', () => {
    renderPanel([session()], { open: false, connections: true });

    expect(screen.queryByText('Session 1')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Connections' })).toHaveAttribute(
      'href',
      '/app/chat/connections?from=thread-1',
    );
  });
});
