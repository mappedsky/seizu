import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ChatSessionsPanel, {
  groupSessionsByRecency,
} from 'src/components/ChatSessionsPanel';
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

describe('groupSessionsByRecency', () => {
  it('files sessions under when they were last active, in order', () => {
    const now = new Date(2026, 9, 22, 15, 0);
    const at = (days: number, hour = 10) =>
      new Date(2026, 9, 22 - days, hour).toISOString();
    const groups = groupSessionsByRecency(
      [
        session({ thread_id: 'a', updated_at: at(0, 14) }),
        session({ thread_id: 'b', updated_at: at(0, 1) }),
        session({ thread_id: 'c', updated_at: at(1) }),
        session({ thread_id: 'd', updated_at: at(5) }),
        session({ thread_id: 'e', updated_at: at(20) }),
        session({ thread_id: 'f', updated_at: at(60) }),
      ],
      now,
    );

    expect(
      groups.map((g) => [g.label, g.sessions.map((s) => s.thread_id)]),
    ).toEqual([
      ['Today', ['a', 'b']],
      ['Yesterday', ['c']],
      ['Previous 7 days', ['d']],
      ['Previous 30 days', ['e']],
      ['Earlier', ['f']],
    ]);
  });
});

describe('ChatSessionsPanel filter', () => {
  afterEach(cleanup);

  const many = Array.from({ length: 9 }, (_, i) =>
    session({
      thread_id: `t-${i}`,
      title: i === 4 ? 'CVE triage' : `Chat ${i}`,
    }),
  );

  it('appears only once the list is long enough to need it', () => {
    renderPanel(many.slice(0, 3));
    expect(
      screen.queryByRole('textbox', { name: 'Filter sessions' }),
    ).not.toBeInTheDocument();
  });

  it('narrows the list by title', () => {
    renderPanel(many);
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter sessions' }), {
      target: { value: 'cve' },
    });
    expect(screen.getByText('CVE triage')).toBeInTheDocument();
    expect(screen.queryByText('Chat 1')).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'Filter sessions' }), {
      target: { value: 'nothing like this' },
    });
    expect(screen.getByText('No sessions match.')).toBeInTheDocument();
  });
});
